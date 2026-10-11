"""The report was mostly waiting, and the ceiling that was meant to bound
spending did not bound it.

Measured on a 400-mention corpus with the model stubbed to a fixed latency,
so only the STRUCTURE of the run shows and not the provider's speed:

    before   4.45s wall,  8.0s of model time,  1.78x parallel
    after    2.27s wall,  8.3s of model time,  3.64x parallel

Two things were wrong, and they are opposites.

**The corpus digest ran alone.** It is the most expensive stage in the run —
19 of the run's 32 calls — and it ran synchronously BEFORE the analyst pool
existed, so for 56% of the wall clock there was nothing else in flight.
Exactly one analyst reads it. It is now a job like any other, with the
deep-insight pass waiting on it instead of the whole report doing so.

**Every model fan-out ran at a third of its width.** `low_memory` is on by
default and it gated the pool sizes: 2 digest workers instead of 6, 3
analysts instead of 8, 3 per-mention workers instead of 10. The reasoning
was that fewer threads protect RAM on a 512MB instance, and these are
threads blocked on an HTTP response — a digest call holds a 16KB prompt and
returns about as much, so six in flight cost under 200KB of the 512MB being
protected. They bought no memory and they cost minutes.

Which leaves the question the bundling was really answering: what stops a
run from firing forty requests at a metered provider at once? Not
`low_memory`, and not `concurrency()`, which clamps each stage SEPARATELY —
two overlapping stages each got the full ceiling, so the real number in
flight was the ceiling times the number of overlapping stages. This file's
sibling note in `llm.py` records why that matters: providers reserve credit
for the whole time a request is in flight, and that is how a working account
started answering HTTP 402. `LLM_MAX_CONCURRENCY` is now one semaphore for
the process, held around the send and nowhere else.
"""

import threading
import time

import pytest

from engine import llm
from engine.config import settings


class _Spy:
    """Records how many calls were in flight at once."""

    def __init__(self, latency=0.02):
        self.latency = latency
        self.lock = threading.Lock()
        self.inflight = 0
        self.peak = 0
        self.labels = []
        self.overlaps = set()

    def __call__(self, prompt, **kwargs):
        label = str(prompt)[:24]
        with self.lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            self.labels.append(label)
            # Which labels were in flight together — used to prove that two
            # stages genuinely overlap rather than merely both having run.
            self.overlaps.add(frozenset(self._live))
        time.sleep(self.latency)
        with self.lock:
            self.inflight -= 1
        return {}

    @property
    def _live(self):
        return [label for label in self.labels[-self.inflight:]] if self.inflight else []


@pytest.fixture
def spy(monkeypatch):
    probe = _Spy()
    monkeypatch.setattr(llm, "_call_json", lambda prompt, **kw: probe(prompt, **kw))
    monkeypatch.setattr(llm, "_breaker_blocks", lambda: False)
    monkeypatch.setattr(llm, "out_of_credit", lambda: False)
    return probe


def _reset_gate():
    llm._GATE = None
    llm._GATE_SIZE = 0


def test_the_ceiling_is_one_limit_for_the_whole_process(spy, monkeypatch):
    """`concurrency()` clamps each stage on its own, so N overlapping stages
    multiplied it. The gate is held around the send, where overlapping
    cannot multiply anything."""
    monkeypatch.setattr(settings, "llm_max_concurrency", 3)
    _reset_gate()
    try:
        threads = [threading.Thread(target=llm.call_json, args=(f"p{i}",))
                   for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert spy.peak <= 3, (
            f"{spy.peak} calls were in flight against a ceiling of 3")
        assert len(spy.labels) == 16, "calls were dropped rather than queued"
    finally:
        _reset_gate()


def test_two_overlapping_stages_cannot_double_the_ceiling(spy, monkeypatch):
    """The exact failure the per-stage clamp allowed: two pools, each
    correctly sized to the ceiling, three times the requests in flight."""
    monkeypatch.setattr(settings, "llm_max_concurrency", 2)
    _reset_gate()
    try:
        def stage(tag):
            pool = [threading.Thread(target=llm.call_json, args=(f"{tag}{i}",))
                    for i in range(6)]
            for thread in pool:
                thread.start()
            for thread in pool:
                thread.join(timeout=30)

        a = threading.Thread(target=stage, args=("a",))
        b = threading.Thread(target=stage, args=("b",))
        a.start(); b.start(); a.join(timeout=30); b.join(timeout=30)
        assert spy.peak <= 2, (
            f"two stages put {spy.peak} calls in flight against a ceiling of 2")
    finally:
        _reset_gate()


def test_no_ceiling_means_no_gate(monkeypatch):
    """Unset is what the paid Anthropic path wants, and a semaphore nobody
    asked for is a queue nobody asked for."""
    monkeypatch.setattr(settings, "llm_max_concurrency", 0)
    _reset_gate()
    assert llm.in_flight_gate() is None


def test_the_gate_is_held_around_the_send_and_not_around_a_stage():
    """A caller that fanned out while holding a slot would deadlock against
    its own children as soon as the ceiling was reached. Asserted on the
    structure, because the failure is a hang and a hanging test is worse
    than no test."""
    import inspect

    source = inspect.getsource(llm.call_json)
    assert "with _in_flight():" in source
    body = source[source.index("with _in_flight():"):]
    # Exactly one statement inside the gate: the send.
    inside = [line for line in body.splitlines()[1:]
              if line.strip() and not line.strip().startswith("#")]
    assert inside and inside[0].strip().startswith("result = _call_json("), inside[:3]
    assert not inside[1].startswith("            "), (
        "more than the send is inside the gate, which risks a nested call "
        "waiting on a slot its own parent is holding")


# --- the structural rules -----------------------------------------------------

def test_low_memory_does_not_decide_how_wide_a_model_fanout_runs():
    """It gated every pool in the product, at a third to a quarter width, on
    by default, to protect memory that threads blocked on a socket do not
    use. Memory protection belongs to NER, YouTube comments and the
    per-mention cap, all of which still honour it."""
    import pathlib

    offenders = []
    for path in ("engine/reports/digest.py", "engine/reports/sections.py",
                 "engine/pipeline.py"):
        for number, line in enumerate(
                pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if "low_memory" not in line or line.strip().startswith("#"):
                continue
            # `low_memory_max_llm_mentions` is a cap on how much WORK is
            # done, not on how wide it runs: it bounds the corpus the
            # per-mention stage touches, which is a real allocation and
            # genuinely belongs to the memory setting. Excluded by name so
            # this check stays about pool widths.
            if "max_llm_mentions" in line:
                continue
            if any(word in line for word in
                   ("workers", "_cap", "concurrency", "ThreadPoolExecutor")):
                offenders.append(f"{path}:{number}: {line.strip()}")
    assert not offenders, (
        "a model fan-out is sized from LOW_MEMORY again: " + "; ".join(offenders))


def test_the_memory_protections_still_honour_low_memory():
    """The other half of that separation. Dropping these would turn a
    latency fix into an OOM on the instance sizes they were written for."""
    import pathlib

    for path, what in (("engine/processing/entities.py", "the spaCy pipeline"),
                       ("engine/ingestion/youtube_connector.py", "YouTube extraction"),
                       ("engine/pipeline.py", "the per-mention work cap")):
        body = pathlib.Path(path).read_text(encoding="utf-8")
        assert "low_memory" in body, f"{what} no longer honours LOW_MEMORY"


# --- the digest no longer blocks the report -----------------------------------

def test_the_digest_runs_alongside_the_analysts(monkeypatch):
    """It is 19 of a 400-mention run's 32 calls, exactly one analyst reads
    it, and it used to run synchronously before the pool existed — so for
    56% of the wall clock there was nothing else in flight.

    Asserted by watching what is in flight TOGETHER, not by timing, because
    a timing assertion on a loaded CI box is a flaky test.
    """
    from datetime import datetime, timedelta

    from engine.reports import sections

    seen_together = threading.Event()
    lock = threading.Lock()
    live: set[str] = set()

    def kind(prompt: str) -> str:
        return "digest" if "distil" in str(prompt)[:120] else "analyst"

    def fake(prompt, **kwargs):
        tag = kind(prompt)
        with lock:
            live.add(tag)
            if len(live) > 1:
                seen_together.set()
        time.sleep(0.05)
        with lock:
            live.discard(tag)
        return {}

    monkeypatch.setattr(llm, "_call_json", fake)
    monkeypatch.setattr(llm, "_breaker_blocks", lambda: False)
    monkeypatch.setattr(llm, "out_of_credit", lambda: False)
    monkeypatch.setattr(settings, "llm_max_concurrency", 0)
    _reset_gate()

    start, end = datetime(2026, 2, 1), datetime(2026, 2, 27)
    mentions = [{
        "id": f"m{i}", "text": "Talon on the health levy and the rollout. " * 20,
        "platform": "news", "source_type": "article",
        "posted_at": start + timedelta(hours=i),
        "author_handle": f"a{i % 5}", "engagement": {"likes": i},
    } for i in range(200)]
    payload = {
        "executive_summary": "",
        "sentiment_breakdown": {"total_mentions_analyzed": 200,
                                "positive_pct": 30, "neutral_pct": 40,
                                "negative_pct": 30},
        "volume_trends": {"total_mentions": 200, "by_day": {},
                          "by_platform": {"news": 200}},
        "influence_summary": [], "narrative_breakdown": [],
    }
    sections.enrich_report_payload("Talon", start, end, payload,
                                   mentions=mentions, narratives=[])

    assert seen_together.is_set(), (
        "no analyst call was ever in flight at the same time as a digest "
        "call — the digest is blocking the report again")


def test_a_failed_digest_fails_one_section_rather_than_hanging_the_report(monkeypatch):
    """The deep-insight pass waits on the digest. If a digest that blew up
    left that wait unsignalled, the pass would hang until the phase
    deadline — the difference between a failed section and a report that
    never arrives, which is the whole reason this codebase has deadlines."""
    from datetime import datetime

    from engine.reports import digest, sections

    monkeypatch.setattr(digest, "build_corpus_digest",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(llm, "_call_json", lambda prompt, **kw: {})
    monkeypatch.setattr(llm, "_breaker_blocks", lambda: False)
    monkeypatch.setattr(llm, "out_of_credit", lambda: False)

    start, end = datetime(2026, 2, 1), datetime(2026, 2, 27)
    payload = {
        "executive_summary": "",
        "sentiment_breakdown": {"total_mentions_analyzed": 1, "positive_pct": 0,
                                "neutral_pct": 100, "negative_pct": 0},
        "volume_trends": {"total_mentions": 1, "by_day": {}, "by_platform": {}},
        "influence_summary": [], "narrative_breakdown": [],
    }
    began = time.monotonic()
    sections.enrich_report_payload(
        "Talon", start, end, payload,
        mentions=[{"id": "m1", "text": "A mention about the levy.",
                   "platform": "news", "posted_at": start,
                   "author_handle": "a", "engagement": {}}],
        narratives=[])
    took = time.monotonic() - began

    assert took < 30, (
        f"the report took {took:.0f}s after a failed digest — the wait is "
        "not being released on failure")
    assert payload["deep_insights"] == {"insights": [], "the_one_thing": ""}, (
        "a failed digest did not leave the deep-insight section at its "
        "fallback")
