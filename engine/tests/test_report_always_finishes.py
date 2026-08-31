"""A report must arrive, even when a section will not.

Live symptom: the page sat on "Still building this report" with Sentiment,
Narratives, Executive summary and Beneath the surface ticked and everything
from Public voice onwards pending — permanently, across repeated runs of any
length. The analyst fan-out used `as_completed(futures)` with NO timeout, so a
single hung call blocked the loop forever and every later stage waited behind
it. No amount of waiting helped, because nothing was going to arrive.

A section that cannot be produced inside the budget is a failed section, and a
report with a failed section beats a report that never comes.
"""

import threading
import time

import pytest

from engine import stages
from engine.config import settings
from engine.reports import sections


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    stages.reset()
    monkeypatch.setattr(settings, "analyst_deadline_seconds", 2, raising=False)
    yield
    stages.reset()


def _run_jobs(jobs, published=None):
    """Drive the same fan-out `enrich_report_payload` uses."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from concurrent.futures import TimeoutError as FuturesTimeout

    payload: dict = {}

    def publish(key, value):
        if published is not None:
            published.append(key)

    def run(key):
        fn, fallback = jobs[key]
        return key, stages.run_guarded(key, fn, fallback=fallback)

    deadline = max(1, settings.analyst_deadline_seconds)
    pool = ThreadPoolExecutor(max_workers=4)
    futures = {pool.submit(run, key): key for key in jobs}
    try:
        for future in as_completed(futures, timeout=deadline):
            key, value = future.result()
            payload[key] = value
            publish(key, value)
    except FuturesTimeout:
        for future, key in futures.items():
            if not future.done():
                stages.current().failed(key, f"did not finish within {deadline}s")
                payload.setdefault(key, jobs[key][1])
                publish(key, payload[key])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return payload


def test_a_hung_section_does_not_block_the_others():
    stop = threading.Event()

    def hangs():
        stop.wait(30)
        return ["never arrives"]

    jobs = {
        "public_voice": (hangs, []),
        "platform_pulse": (lambda: [{"platform": "x"}], []),
        "timeline": (lambda: [{"date": "2026-08-01"}], []),
    }
    started = time.monotonic()
    payload = _run_jobs(jobs)
    elapsed = time.monotonic() - started
    stop.set()

    assert elapsed < 10, f"the fan-out waited {elapsed:.0f}s on a hung section"
    assert payload["platform_pulse"], "a healthy section was lost with the hung one"
    assert payload["timeline"]


def test_the_hung_section_is_named_as_failed_not_left_blank():
    stop = threading.Event()
    jobs = {"public_voice": (lambda: stop.wait(30) or [], []),
            "timeline": (lambda: [{"date": "x"}], [])}
    _run_jobs(jobs)
    stop.set()

    failed = [record.name for record in stages.current().failures]
    assert "public_voice" in failed
    reason = stages.current().records["public_voice"].error
    assert "did not finish" in reason, "the reader must be told it was abandoned"


def test_an_abandoned_section_still_gets_its_fallback_value():
    stop = threading.Event()
    jobs = {"public_voice": (lambda: stop.wait(30) or ["late"], ["fallback"])}
    payload = _run_jobs(jobs)
    stop.set()
    assert payload["public_voice"] == ["fallback"], "downstream readers need a value"


def test_every_section_is_published_even_when_abandoned():
    stop = threading.Event()
    published: list[str] = []
    jobs = {"public_voice": (lambda: stop.wait(30) or [], []),
            "timeline": (lambda: [{"d": 1}], [])}
    _run_jobs(jobs, published=published)
    stop.set()
    assert set(published) == {"public_voice", "timeline"}, (
        "a section the reader is waiting on must resolve one way or the other")


def test_a_healthy_fan_out_is_untouched_by_the_deadline():
    jobs = {f"s{i}": (lambda i=i: [{"n": i}], []) for i in range(6)}
    payload = _run_jobs(jobs)
    assert len(payload) == 6
    assert stages.current().failures == []


# --- the wiring must actually carry a deadline -------------------------------

def test_the_real_fan_out_passes_a_timeout():
    import inspect

    source = inspect.getsource(sections.enrich_report_payload)
    assert "as_completed(futures, timeout=" in source, (
        "the fan-out has no deadline; one hung analyst blocks the report forever")
    assert "cancel_futures=True" in source, (
        "waiting on the stragglers reintroduces the hang at shutdown")


def test_the_deadline_is_configurable_and_has_a_floor():
    assert settings.analyst_deadline_seconds is not None
    from engine.config import Settings

    assert Settings().analyst_deadline_seconds >= 300, (
        "the default must leave a slow but working model room to finish")


# --- the per-call budget cannot outlive the fan-out that contains it ---------

def test_a_single_call_cannot_outlast_the_analyst_deadline():
    from engine import llm
    from engine.config import Settings

    worst_case = llm.OPENAI_COMPATIBLE_TOTAL_BUDGET
    assert worst_case < Settings().analyst_deadline_seconds, (
        f"one call may take {worst_case}s inside a {Settings().analyst_deadline_seconds}s "
        "fan-out — retries can consume the whole budget")


def test_the_per_attempt_timeout_is_shorter_than_the_total_budget():
    from engine import llm

    assert llm.OPENAI_COMPATIBLE_TIMEOUT < llm.OPENAI_COMPATIBLE_TOTAL_BUDGET


def test_the_analyst_window_is_tunable_without_a_redeploy():
    """160k characters is ~40k tokens: a free-tier model with a small context
    or a hard rate cap either refuses it or queues it until something upstream
    gives up."""
    from engine.reports.analysts import corpus_chars_per_call

    assert corpus_chars_per_call() == settings.analyst_corpus_chars
