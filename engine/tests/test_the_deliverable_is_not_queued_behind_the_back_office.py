"""The client's deliverable was built last, behind work its reader never opens.

A live report on Rigathi Gachagua, build 06c1a53: "Overview" and "Weekly
Dashboard" on the page, no Sentiment Framework tab at all, and the run going
on and on behind them. Six deployments had tried to fix that by shortening
the queue in front of the framework — deadlines on the analysts, ceilings on
the tail, wider pools. Every one of them was correct and none of them helped,
because the framework was still at the back of the queue: after the analyst
fan-out, after entity and event resolution, after the knowledge graph, and
gated on whatever time those left.

It needs none of them. Section by section it is arithmetic over the scored
corpus, the storylines and the emergent items — all of which exist before the
first analyst is asked anything. The analysts add evidence to it; they are
not what it is made of.

So the fix is order, not speed:

  * the framework is built and published BEFORE the analyst fan-out, and
    refined after it — never gated on the clock, because a section that costs
    milliseconds and IS the deliverable is the last thing a deadline should
    take away;
  * the back office — resolution, the knowledge graph, the claim audit, the
    investigator — runs AFTER the report is delivered, on sessions of its
    own, against its own ceiling;
  * and because a reader can start another report while the last one's
    back office is still running, each thread records into the run that
    started it, not into whichever report happens to be current.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from engine import health, stages
from engine.config import settings
from engine.db.models import Entity, IntelligenceReport
from engine import pipeline
from engine.tests.test_end_to_end_run import WINDOW_DAYS, _seed, scripted_model  # noqa: F401


def _run(db_session, subject, **kwargs):
    now = datetime.utcnow()
    return pipeline.run_analysis(db_session, subject, "weekly",
                                 now - timedelta(days=WINDOW_DAYS), now, **kwargs)


@pytest.fixture()
def back_office_on(monkeypatch):
    monkeypatch.setattr(settings, "enable_resolution", True)
    monkeypatch.setattr(settings, "enable_verification", True)


# --- the framework is first --------------------------------------------------

def test_the_framework_is_on_the_page_before_any_analyst_is_asked(
        db_session, scripted_model, monkeypatch):
    published: list[str] = []
    seen_when_analysts_began: list[str] = []
    real = pipeline.enrich_report_payload

    def watching(*args, **kwargs):
        seen_when_analysts_began.extend(published)
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "enrich_report_payload", watching)
    subject = _seed(db_session)
    _run(db_session, subject, on_section=lambda key, value: published.append(key))

    assert seen_when_analysts_began, "the analyst phase never started"
    assert "sentiment_framework" in seen_when_analysts_began, (
        "the client's deliverable waited for the analysts — so anything slow "
        "ahead of it starves it, which is the bug")


def test_the_framework_is_refined_with_what_the_analysts_wrote(db_session, scripted_model):
    published: list[str] = []
    subject = _seed(db_session)
    report = _run(db_session, subject, on_section=lambda key, value: published.append(key))

    assert published.count("sentiment_framework") >= 2, (
        "built once, before the analysts — so it never carries their evidence")
    framework = report.payload["sentiment_framework"]
    assert framework["framework"] == "Sentiment Analysis Framework V1.0"
    # The executive summary is an analyst's work; only the refined pass has it.
    assert framework["summary_of_subject"]["executive_summary"]


def test_a_failed_refinement_keeps_the_first_version(db_session, scripted_model, monkeypatch):
    """A framework without the deep-dive evidence beats no framework at all —
    and it must not be reported as a failed section, because it is not one."""
    from engine.reports import sentiment_framework

    real = sentiment_framework.build
    calls = {"n": 0}

    def second_call_fails(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("refinement broke")
        return real(*args, **kwargs)

    monkeypatch.setattr(sentiment_framework, "build", second_call_fails)
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload

    assert payload.get("sentiment_framework"), "the first version was thrown away"
    assert "sentiment_framework" not in payload["section_status"]["failed"]


def test_the_framework_is_never_gated_on_the_clock():
    """It costs milliseconds. Skipping it "for time" is how a report came to
    have every slow section and not the one its client asked for."""
    import inspect

    source = inspect.getsource(pipeline)
    assert 'out_of_time("sentiment_framework")' not in source
    assert '_bounded("sentiment_framework"' not in source


def test_the_framework_touches_no_database():
    """It runs before the analysts and again after them, when the session's
    connection has sat idle through the whole model phase. The previous
    report is read ONCE, before that phase, and handed in."""
    import inspect

    signature = inspect.signature(pipeline._build_framework)
    assert "db" not in signature.parameters
    run = inspect.getsource(pipeline.run_analysis)
    assert run.index("prior_payload = _previous_payload(db, politician)") < run.index(
        "release_connection(db)")


# --- the back office is last, and after delivery ------------------------------

def test_the_back_office_waits_until_the_report_is_delivered(
        db_session, scripted_model, back_office_on, monkeypatch):
    deferred: list = []
    subject = _seed(db_session)
    report = _run(db_session, subject, defer_back_office=deferred)

    assert len(deferred) == 1, "the back office was not handed back to the caller"
    for key in ("verification", "claims", "resolution"):
        assert key not in report.payload, f"{key} ran in front of the reader"
    assert report.payload["back_office"]["status"] == "pending"

    # The caller runs it afterwards, on sessions of its own.
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False,
                           autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("engine.db.session.SessionLocal", factory)
    pipeline.run_back_office(deferred[0])

    check = factory()
    try:
        stored = check.get(IntelligenceReport, report.id).payload
    finally:
        check.close()
    assert stored["back_office"]["status"] == "done"
    assert "verification" in stored, "the claim audit never reached the stored report"
    assert stored.get("sentiment_framework"), "the enriched report lost the deliverable"


def test_inline_callers_still_get_everything(db_session, scripted_model, back_office_on):
    """Scripts and tests call run_analysis without deferring, and must get the
    report they always got."""
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    assert payload["back_office"]["status"] == "done"
    assert "verification" in payload


def test_a_late_back_office_records_into_its_own_run(
        db_session, scripted_model, back_office_on, monkeypatch):
    """By the time the back office runs, another report may have started and
    replaced the process-wide ledger. Its failures belong to its own report —
    shown on another one, they are a failure from one report presented as a
    finding about a different subject."""
    deferred: list = []
    subject = _seed(db_session)
    _run(db_session, subject, defer_back_office=deferred)
    task = deferred[0]

    stages.reset()   # a new report begins
    health.reset()
    newer_ledger = stages.current()

    from engine.agents import resolve

    def broken(*args, **kwargs):
        raise RuntimeError("resolution broke")

    monkeypatch.setattr(resolve, "resolve_corpus", broken)
    pipeline.run_back_office(task, session=db_session)

    assert "entity_event_resolution" in [r.name for r in task.ledger.failures]
    assert not newer_ledger.failures, "the old report's failure landed on the new one"


def test_a_worker_thread_records_into_the_run_that_started_it():
    from concurrent.futures import ThreadPoolExecutor

    from engine.runctx import carry, run_context

    mine, theirs = stages.StageLedger(), stages.StageLedger()
    tracker = health.RunHealth()
    stages.reset()
    with run_context(mine, tracker):
        work = carry(lambda: stages.current().failed("x", "boom"))
    with stages.bound(theirs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(work).result()
    assert [r.name for r in mine.failures] == ["x"]
    assert not theirs.failures and not stages.current().failures


def test_the_api_delivers_then_runs_the_back_office():
    """Structural, because the API job is a thread with a database behind it:
    the report is marked done BEFORE the back office starts, and the back
    office can never flip it back to running."""
    import inspect

    from engine import api_server

    job = inspect.getsource(api_server._run_report_job)
    assert "defer_back_office=back_office" in job
    assert job.index('"status": "done", "ok": True') < job.index("_back_office_after_delivery(")
    after = inspect.getsource(api_server._back_office_after_delivery)
    assert 'status="running"' not in after


def test_the_flush_is_not_dropped_for_the_deliverable():
    """The partial-publish throttle DROPS an update inside its interval, so the
    burst of sections before a long silent phase sat unseen for minutes."""
    from engine import api_server

    assert "sentiment_framework" in api_server._FLUSH_ON_SECTIONS
    assert "section_status" in api_server._FLUSH_ON_SECTIONS


# --- a racing writer does not kill a run -------------------------------------

def test_a_racing_writer_of_the_same_entity_does_not_kill_the_run(db_session, monkeypatch):
    """Check-then-insert loses to any writer that lands between the two — and
    the previous report's back office is now exactly such a writer."""
    key = pipeline._canonical_key("person", "Jane Mwangi")
    existing = Entity(name="Jane Mwangi", type="person", canonical_key=key)
    db_session.add(existing)
    db_session.commit()

    real_query = db_session.query
    blinded = {"done": False}

    class _Blind:
        def filter_by(self, **_):
            return self

        def first(self):
            return None

    def query(*args, **kwargs):
        if args and args[0] is Entity and not blinded["done"]:
            blinded["done"] = True
            return _Blind()   # the race: the row exists, the check missed it
        return real_query(*args, **kwargs)

    monkeypatch.setattr(db_session, "query", query)
    got = pipeline._upsert_entity(db_session, "person", "Jane Mwangi")
    assert got.id == existing.id
    # And the session is still usable afterwards.
    assert real_query(Entity).filter_by(canonical_key=key).count() == 1
