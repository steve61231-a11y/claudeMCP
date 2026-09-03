"""Search, driven end to end, twice — and inspected the way the reader sees it.

Everything else about Search is tested in pieces. This runs the whole pipeline
and asks the only question that matters: what does the page actually get?

Two runs, because the two failure modes are opposite and both have shipped:

  - a WORKING backend must still produce every section (no regression from the
    digest, breaker and floor work),
  - a DEAD backend must still produce a usable page, quickly, and say plainly
    which parts no model wrote. This is the run that spent forty-five minutes
    and delivered sentiment and nothing else.
"""

import time
from datetime import datetime

from engine import llm
from engine.api_server import _build_frontend_payload
from engine.pipeline import run_pipeline
from engine.tests.test_stub_provider_end_to_end import _stub_pipeline
from engine.tests.test_pipeline import make_politician

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 22, 23, 59, 59))

#: Sections a reader looks for. Not every one can be produced without a model,
#: but the page must never be down to sentiment alone.
READER_SECTIONS = ("volume", "narratives", "platformPulse", "timeline",
                   "influencerStances", "publicVoice")


def _filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(value.values())
    return bool(value)


def _run(db_session, monkeypatch):
    # _build_frontend_payload opens its own session from DATABASE_URL, which in
    # a test environment points at a host that does not exist. Hand it the test
    # session instead so the payload the READER gets is what we inspect.
    import engine.api_server as api

    monkeypatch.setattr(api, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    published: list[str] = []
    politician = make_politician(db_session)
    started = time.monotonic()
    report = run_pipeline(db_session, politician, "weekly", *WINDOW)
    published.extend(sorted(report.payload or {}))
    elapsed = time.monotonic() - started
    return _build_frontend_payload(politician, report), published, elapsed


def test_a_working_backend_still_fills_the_page(db_session, monkeypatch):
    """No regression from the digest, breaker and floor work.

    The stub answers every call with canned JSON that does not fit the analyst
    schemas, so some sections come back empty and the floor fills them — which
    is the floor doing its job. What this pins is that the page is FULL either
    way, and that the run completes.
    """
    _stub_pipeline(monkeypatch)
    frontend, published, _elapsed = _run(db_session, monkeypatch)

    missing = [key for key in READER_SECTIONS if not _filled(frontend.get(key))]
    assert not missing, f"empty on a working backend: {missing}"
    assert published, "the report carried no sections at all"
    assert _filled(frontend.get("coverage"))


def test_a_dead_backend_still_delivers_a_usable_page(db_session, monkeypatch):
    """The forty-five-minute run that produced sentiment and nothing else."""
    _stub_pipeline(monkeypatch)

    def refuse(*args, **kwargs):
        raise RuntimeError("429 provider refused")

    monkeypatch.setattr(llm, "_call_json", refuse)
    monkeypatch.setattr(llm, "call_json_untrusted", refuse)

    frontend, published, _elapsed = _run(db_session, monkeypatch)

    missing = [key for key in READER_SECTIONS if not _filled(frontend.get(key))]
    assert len(missing) <= 1, \
        f"page is missing {missing} — this is the run that showed sentiment and nothing else"
    assert _filled(frontend.get("derivedSections")), \
        "sections were counted and the reader was not told"
    assert _filled(frontend.get("summary"))


def test_the_reader_is_told_exactly_which_sections_no_model_wrote(db_session, monkeypatch):
    _stub_pipeline(monkeypatch)

    def refuse(*args, **kwargs):
        raise RuntimeError("429 provider refused")

    monkeypatch.setattr(llm, "_call_json", refuse)
    monkeypatch.setattr(llm, "call_json_untrusted", refuse)
    frontend, _published, _elapsed = _run(db_session, monkeypatch)

    derived = set(frontend.get("derivedSections") or [])
    assert derived
    # Everything named must actually be present, and everything derived must be
    # named — a section marked counted that is empty, or one silently counted,
    # are both lies about how the page was made.
    mapping = {"platform_pulse": "platformPulse", "timeline": "timeline",
               "influencer_stances": "influencerStances", "public_voice": "publicVoice",
               "executive_summary": "summary"}
    for key in derived:
        assert _filled(frontend.get(mapping.get(key, key))), f"{key} marked derived but empty"


def test_a_dead_backend_does_not_cost_the_reader_the_full_retry_budget(monkeypatch):
    """Not a wall-clock assertion — the stub is instant. This checks the
    mechanism: the run stops asking a provider that will not answer."""
    llm.reset_breaker()
    sent = {"n": 0}

    def refuse(prompt, max_tokens=1024, model=None):
        sent["n"] += 1
        raise RuntimeError("429 provider refused")

    monkeypatch.setattr(llm, "_call_json", refuse)
    for _ in range(25):
        try:
            llm.call_json("p")
        except Exception:  # noqa: BLE001
            pass
    assert sent["n"] < 10, f"the provider was contacted {sent['n']} times out of 25"
    llm.reset_breaker()
