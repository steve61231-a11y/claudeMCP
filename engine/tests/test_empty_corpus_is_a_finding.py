"""A run that collects nothing must say so.

Driving Search end to end through the real API found this: with every source
blocked, the run returned ok=True, filled seventeen scaffolding keys, rendered
no sections and explained nothing. To the reader that is "Search not working",
and it is indistinguishable from a subject nobody has written about — which is
the opposite finding.
"""

from datetime import datetime, timedelta

import pytest

from engine.tests.test_pipeline import make_politician


@pytest.fixture(autouse=True)
def _stub_backend(monkeypatch):
    """Preflight stops a run before collection when the model is unreachable.
    That is correct and separately tested; here the subject is the corpus."""
    from engine.config import settings as config_settings

    monkeypatch.setattr(config_settings, "llm_provider", "stub")


def _run(db_session):
    from engine.pipeline import run_analysis

    end = datetime.utcnow()
    return run_analysis(db_session, make_politician(db_session), "weekly",
                        end - timedelta(days=30), end)


def test_an_empty_corpus_produces_a_stated_finding(db_session):
    payload = _run(db_session).payload
    finding = payload.get("nothing_collected")

    assert finding, "an empty run said nothing about being empty"
    assert finding["why"]
    assert finding["window"]
    assert finding["guidance"], "the reader is told nothing to try"


def test_it_reaches_the_page(db_session, monkeypatch):
    import engine.api_server as api
    from engine.api_server import _build_frontend_payload

    # _build_frontend_payload opens its own session from DATABASE_URL, which in
    # a test environment names a host that does not exist. Hand it the test
    # session instead. IMPORTANT: do not stub out session.close() to survive
    # _build_frontend_payload's internal `finally: db.close()` calls — that
    # patches the instance itself, so it also disables the db_session
    # FIXTURE's own teardown close(), leaving the transaction open and
    # deadlocking the migration downgrade that runs right after. A SQLAlchemy
    # Session is safe to close more than once (it just autobegins again on
    # next use), so no patch is needed here at all.
    monkeypatch.setattr(api, "SessionLocal", lambda: db_session)

    politician = make_politician(db_session)
    from engine.pipeline import run_analysis

    end = datetime.utcnow()
    report = run_analysis(db_session, politician, "weekly",
                          end - timedelta(days=30), end)
    frontend = _build_frontend_payload(politician, report)
    assert frontend.get("nothingCollected"), "the finding never reached the reader"


def test_the_page_says_it_rather_than_rendering_empty_sections():
    html = open("web/pulse_app.html", encoding="utf-8").read()
    assert "nothingCollected" in html
    assert "No mentions were collected" in html
    # And it must not be mistaken for a claim about the subject.
    assert "not a finding that the subject has no coverage" in html


def test_a_run_with_mentions_carries_no_such_finding(db_session):
    """It must never appear on a run that did collect something."""
    import hashlib

    from engine.db.models import RawMention
    from engine.pipeline import run_analysis

    politician = make_politician(db_session)
    now = datetime.utcnow()
    for i in range(6):
        text = f"{politician.name} and the National Treasury discussed the loans {i}"
        db_session.add(RawMention(
            politician_id=politician.id, platform="news", source_type="article",
            author_handle=f"@voice{i}", text=text, posted_at=now - timedelta(days=i),
            raw_payload={"url": f"https://n/{i}"}, engagement_json={"likes": i},
            content_hash=hashlib.sha1(text.encode()).hexdigest(),
            source_url=f"https://n/{i}"))
    db_session.commit()

    payload = run_analysis(db_session, politician, "weekly",
                           now - timedelta(days=30), now).payload
    assert not payload.get("nothing_collected")
