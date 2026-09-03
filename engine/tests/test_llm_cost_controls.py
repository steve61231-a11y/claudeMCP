"""The two things that decide what a report costs.

Testing is where the money goes: the same subject re-run over a corpus that has
barely moved, paying full price each time. These pin the controls — the corpus
map step runs on the bulk tier, and an identical call is replayed rather than
re-bought — because both are invisible when they regress. Nothing errors; the
bill just doubles.
"""

import json

import pytest

from engine import llm


class _FakeResponse:
    def __init__(self, text):
        self.content = [type("B", (), {"text": text})()]
        self.stop_reason = "end_turn"
        self.usage = None


@pytest.fixture()
def recorder(monkeypatch):
    """Records every model actually billed, and answers with valid JSON."""
    calls = []

    class _Messages:
        def create(self, model, max_tokens, messages):
            calls.append({"model": model, "prompt": messages[0]["content"]})
            return _FakeResponse(json.dumps({"digest": {"claims": []}, "ok": True}))

    monkeypatch.setattr(llm, "get_client", lambda: type("C", (), {"messages": _Messages()})())
    monkeypatch.setattr(llm, "_record_usage", lambda response: None)
    return calls


# --- the map step must not run on the strong model ------------------------

def test_corpus_map_step_runs_on_the_bulk_tier(recorder, monkeypatch):
    """One call per chunk of the whole corpus — the highest-volume stage there
    is, and mechanical extraction. On the strong model it is what makes reading
    everything unaffordable."""
    from engine.reports import digest as digest_module

    monkeypatch.setattr(llm.settings, "anthropic_model", "strong-model")
    monkeypatch.setattr(llm.settings, "anthropic_bulk_model", "cheap-model")

    mentions = [{"id": f"m{i}", "platform": "x", "text": "some text " * 40,
                 "author_handle": "a", "posted_at": None, "engagement": {}}
                for i in range(40)]
    # A corpus that fits in one analyst window is sent whole and never
    # compressed, so the map step would make no calls at all and this would
    # assert about a stage that did not run. Shrink the window to force it.
    monkeypatch.setattr(digest_module, "DIGEST_CONTEXT_CHARS", 4000)
    digest_module.build_corpus_digest("Subject", mentions)

    assert recorder, "the map step made no calls at all"
    assert {c["model"] for c in recorder} == {"cheap-model"}


# --- an identical call is replayed, not re-bought --------------------------

def test_identical_call_is_served_from_cache(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))

    first = llm.call_json("the same prompt", max_tokens=100, model="m")
    second = llm.call_json("the same prompt", max_tokens=100, model="m")

    assert first == second
    assert len(recorder) == 1, "the second identical call was paid for again"


def test_cache_key_separates_prompt_model_and_budget(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))

    llm.call_json("prompt A", max_tokens=100, model="m")
    llm.call_json("prompt B", max_tokens=100, model="m")   # different prompt
    llm.call_json("prompt A", max_tokens=100, model="n")   # different model
    llm.call_json("prompt A", max_tokens=200, model="m")   # different budget

    assert len(recorder) == 4


def test_cache_is_off_unless_explicitly_enabled(recorder, monkeypatch):
    """Production must never be served a stale answer."""
    monkeypatch.delenv("LLM_CACHE_DIR", raising=False)

    llm.call_json("prompt", max_tokens=100, model="m")
    llm.call_json("prompt", max_tokens=100, model="m")

    assert len(recorder) == 2


def test_unwritable_cache_never_fails_the_run(recorder, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_DIR", "/proc/nonexistent/cannot-write-here")

    assert llm.call_json("prompt", max_tokens=100, model="m") == {"digest": {"claims": []}, "ok": True}


def test_a_failed_call_is_never_remembered_as_an_answer(tmp_path, monkeypatch):
    """A cached failure would be paid for once and then be wrong forever."""
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))

    class _Boom:
        def create(self, **kwargs):
            raise RuntimeError("upstream failure")

    monkeypatch.setattr(llm, "get_client", lambda: type("C", (), {"messages": _Boom()})())
    with pytest.raises(RuntimeError):
        llm.call_json("prompt", max_tokens=100, model="m")

    assert list(tmp_path.iterdir()) == []


def test_per_mention_workers_respect_the_llm_concurrency_ceiling(monkeypatch):
    """The ceiling exists to keep a rate-limited backend under its per-minute
    quota. The per-mention stage — the highest-volume one in the pipeline —
    was the one place that ignored it and span up ten threads regardless.
    """
    from engine.config import settings as _settings
    from engine.pipeline import _per_mention_workers

    monkeypatch.setattr(_settings, "low_memory", False, raising=False)

    monkeypatch.setattr(_settings, "llm_max_concurrency", 0, raising=False)
    unbounded = _per_mention_workers()
    assert unbounded > 1  # the paid path is not throttled

    monkeypatch.setattr(_settings, "llm_max_concurrency", 2, raising=False)
    assert _per_mention_workers() == 2

    # A ceiling above the default never *raises* the thread count — the
    # memory limit still applies.
    monkeypatch.setattr(_settings, "llm_max_concurrency", 99, raising=False)
    assert _per_mention_workers() == unbounded
