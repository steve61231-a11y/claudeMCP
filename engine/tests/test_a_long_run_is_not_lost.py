"""Four hundred seconds of work, thrown away by one query nobody wrote.

A live run collected a corpus, digested it, completed the analysts — and
then failed with:

    OperationalError: (psycopg2.OperationalError) SSL connection has been
    closed unexpectedly
    [SQL: SELECT ingestion_runs.id AS ingestion_runs_id, ...]

That SQL is an ORM **refresh** of a single `IngestionRun` row, and it was
reproduced exactly. The mechanism:

1. `sessionmaker` expires every instance on commit, by default.
2. Collection commits, which expires the `ingestion_run` object.
3. The run spends minutes in the model phase.
4. The pipeline then reads `ingestion_run.status` and `.stats` to build the
   provenance block — and that read silently re-SELECTs the whole row on a
   pooled connection that has been idle the entire time. Neon had closed it.

`pool_pre_ping` cannot help: it validates at CHECKOUT, and an open
transaction holds one connection for the whole phase.

The query had no reason to exist. The values were already in memory and
nothing else writes that row. So the first fix is to stop commits expiring
instances, which removes a whole class of invisible SELECTs that fire
minutes after the statement that caused them — which is also why the
failure named a table nobody in the stack trace had asked about.

The second fix is the one the reader feels. Sections are saved as they land,
so a run that dies at the end still has most of a report in the store. It
was being discarded in favour of a stack trace: the work done, the model
calls paid for, the wait endured, and nothing handed over. That is the
product's founding rule at the scale of a whole run — a run that failed must
not look like a run that found nothing, and must not look like one that
never happened either.
"""

from datetime import datetime

import pytest

from engine.db.models import IngestionRun, Politician


def test_a_commit_does_not_expire_what_the_run_still_needs(db_session):
    """The crash, at its root. Reading an attribute after a commit must not
    go back to the database — the connection it would use may have been
    closed minutes ago, during the model phase."""
    from sqlalchemy import event

    from engine.db.session import SessionLocal

    assert SessionLocal.kw.get("expire_on_commit") is False, (
        "commits still expire instances, so any attribute read after one "
        "emits a SELECT on a possibly-dead connection")

    politician = Politician(name="Expiry Probe")
    db_session.add(politician)
    db_session.commit()

    run = IngestionRun(politician_id=politician.id,
                       window_start=datetime(2026, 1, 1),
                       window_end=datetime(2026, 2, 1), status="pending",
                       stats={"social_tier": "free"})
    db_session.add(run)
    db_session.commit()

    # Watch for the refresh the live run died on.
    emitted: list[str] = []

    @event.listens_for(db_session.bind, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        if "ingestion_runs" in statement and statement.lstrip().upper().startswith("SELECT"):
            emitted.append(statement)

    try:
        # Exactly what the pipeline does when it builds data_provenance.
        _ = run.status, run.credits_spent, run.stats
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _record)

    assert not emitted, (
        "reading the run's own attributes still re-queries the database: "
        + emitted[0][:120])


# --- the work is delivered, not discarded ------------------------------------

def test_a_failed_run_hands_over_the_sections_it_finished(monkeypatch):
    """The outcome the reader cares about. Eleven finished sections and a
    database error at the end is still eleven sections."""
    import engine.api_server as api

    saved = {"payload": {
        "executive_summary": "A summary that was produced.",
        "sentiment_breakdown": {"total_mentions_analyzed": 612},
        "narrative_breakdown": [{"label": "SHA rollout pain"}],
        "volume_trends": {"total_mentions": 612},
    }}
    monkeypatch.setattr(api, "_read_progress", lambda *a, **k: saved)

    partial = api._partial_payload("William Ruto")
    assert partial is not None
    assert partial["executive_summary"].startswith("A summary")


def test_nothing_produced_is_still_reported_as_a_failure(monkeypatch):
    """"The run failed and produced nothing" and "the run failed after
    producing most of a report" are different outcomes. A recovery path that
    dressed the first up as the second would be inventing a report."""
    import engine.api_server as api

    for empty in ({}, {"payload": None}, {"payload": {"executive_summary": ""}}):
        monkeypatch.setattr(api, "_read_progress", lambda *a, **k: empty)
        assert api._partial_payload("Nobody") is None


def test_the_recovery_path_never_raises(monkeypatch):
    """It runs while the run is already failing. An exception here replaces
    a useful diagnosis with a useless one."""
    import engine.api_server as api

    def _boom(*a, **k):
        raise RuntimeError("the progress store is also down")

    monkeypatch.setattr(api, "_read_progress", _boom)
    assert api._partial_payload("William Ruto") is None


def test_an_incomplete_report_says_so_on_the_page():
    """The one thing that must not happen now that partial reports are
    delivered: a reader taking one for a complete report."""
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    assert "r.partialFailure" in page, "the page does not read the flag"
    assert "This report is incomplete" in page
    assert "resumes from stored progress" in page, (
        "the banner does not tell the reader what to do about it")


def test_the_flag_reaches_the_page_from_the_payload():
    import inspect

    import engine.api_server as api

    body = inspect.getsource(api)
    assert '"partialFailure": payload.get("partial_failure")' in body, (
        "the engine records the partial failure and never sends it")
