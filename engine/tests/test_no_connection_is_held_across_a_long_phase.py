"""The same error twice, because the first fix was next to the cause.

    OperationalError: SSL connection has been closed unexpectedly
    [SQL: SELECT ingestion_runs.id AS ingestion_runs_id, ...]

`execute_run` did this:

    task_ids = [... for (t_id,) in db.query(IngestionTask.id)...]   # opens a
                                                                   # transaction
    with ThreadPoolExecutor(...) as pool:
        list(pool.map(...))          # MINUTES of scraping
    db.expire_all()
    run = db.get(IngestionRun, run_id)   # the SELECT in the error

The query before the pool opens a read transaction, which PINS one pooled
connection to that session. The pool then scrapes for minutes — GDELT alone
is paced at one request per five seconds — and the connection sits idle and
unused throughout. Serverless Postgres closes it, and the first statement
afterwards dies naming a query nobody wrote.

Three things had made this look handled and none of them could help:

- `pool_pre_ping` validates a connection at CHECKOUT. A held connection is
  never checked out again.
- `pool_recycle` retires a connection at checkout too. Same reason.
- `expire_on_commit=False` stops a COMMIT expiring instances. `expire_all()`
  is an explicit expiry and ignores it entirely.

So the defences were real, correctly configured, and in the wrong place —
which is worse than their absence, because they read as proof the problem
was dealt with.

The fix is one line and it is about ownership, not configuration: end the
transaction before the long phase, so the connection goes back to the pool
and the read afterwards checks out a fresh one that pre-ping has validated.

The rule, stated generally: **no session may hold a connection across a
phase that does not use it.** That is what this file checks, by driving the
real `execute_run` and asserting on the session's transaction state at the
moment the pool starts.
"""

from datetime import datetime

import pytest

from engine.config import settings
from engine.db.models import IngestionRun, Politician
from engine.ingestion import orchestrator

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59))


@pytest.fixture
def subject(db_session):
    politician = Politician(
        name="Connection Probe", aliases=[], keywords=[], titles=[],
        swahili_terms=[], tracked_hashtags=[], social_handles={},
    )
    db_session.add(politician)
    db_session.commit()
    return politician


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network: this is about connection lifetime, not collectors."""
    # Discovered, not listed — see conftest.source_flags().
    from engine.tests.conftest import source_flags
    for flag in source_flags():
        monkeypatch.setattr(settings, flag, False, raising=False)
    monkeypatch.setattr(settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(settings, "newsapi_key", "")


def test_the_session_holds_no_connection_while_the_pool_scrapes(
        db_session, subject, monkeypatch):
    """The property, asserted at the only moment it matters: when the long
    phase begins.

    A session inside a transaction owns a connection. If it owns one here,
    that connection spends the whole of collection idle, and every pooled
    defence is looking the other way.
    """
    run = orchestrator.plan_run(db_session, subject, *WINDOW)

    observed = {}

    real_executor = orchestrator.ThreadPoolExecutor

    class _Watching(real_executor):
        def __init__(self, *a, **k):
            # The state of the caller's session as the pool is created.
            observed["in_transaction"] = db_session.in_transaction()
            super().__init__(*a, **k)

    monkeypatch.setattr(orchestrator, "ThreadPoolExecutor", _Watching)
    monkeypatch.setattr(orchestrator, "_execute_task_safe", lambda *a, **k: None)

    orchestrator.execute_run(db_session, run.id)

    assert observed.get("in_transaction") is False, (
        "the session is inside a transaction when the scraping pool starts, "
        "so it is holding a pooled connection that will sit idle for minutes "
        "and be closed by the server before the next statement uses it")


def test_the_read_after_the_pool_still_works(db_session, subject, monkeypatch):
    """Releasing the connection must not cost the run its state. The whole
    point of the read after the pool is to finalise the run."""
    run = orchestrator.plan_run(db_session, subject, *WINDOW)
    monkeypatch.setattr(orchestrator, "_execute_task_safe", lambda *a, **k: None)

    finished = orchestrator.execute_run(db_session, run.id)

    assert finished.id == run.id
    assert finished.status in ("completed", "partial", "failed")
    assert finished.finished_at is not None, (
        "the run was never finalised, so the connection release lost the "
        "write it was protecting")


def test_the_pooled_defences_are_still_configured():
    """They are correct and they are not sufficient. Removing them because
    they did not catch this would be the wrong lesson: they handle the
    checkout case, which is every other query in the product."""
    from engine.db.session import SessionLocal, engine

    assert engine.pool._pre_ping is True
    assert engine.pool._recycle > 0
    assert SessionLocal.kw.get("expire_on_commit") is False


# --- the same rule at the other long phase -----------------------------------

def test_the_analyst_fanout_is_entered_with_no_connection_held():
    """Collection is not the only phase that takes minutes and touches no
    database. The analyst fan-out is the other, and it had the same shape:
    a query, then minutes of model calls, then more queries.

    Fixing only the place the error happened to surface is how this
    codebase has repeatedly paid twice for one bug, so the rule is applied
    at both seams and checked at both.
    """
    import inspect

    from engine import pipeline

    body = inspect.getsource(pipeline.run_analysis)
    call = body.index("enrich_report_payload(")
    before = body[:call]
    assert "release_connection(db)" in before, (
        "the analyst fan-out is entered with a transaction possibly open, so "
        "its connection sits idle for minutes")
    # And it must be the LAST database act before the phase, not an early one.
    assert "release_connection(db)" in before[-600:], (
        "the release happens too early — a later query reopens a transaction "
        "and the connection is held again")


def test_the_rule_has_a_name_so_it_can_be_found():
    """Two one-line commits in two files is a fix nobody can grep for. The
    reasoning lives with the helper, and both call sites point at it."""
    from engine.db import session

    assert callable(session.release_connection)
    doc = session.release_connection.__doc__ or ""
    assert "pre_ping" in doc or "pre-ping" in doc, (
        "the helper does not explain why the pooled defences cannot cover "
        "this, which is the part that made it look handled")


def test_releasing_never_raises(monkeypatch):
    """It runs at a seam between two expensive phases. An exception here
    throws away the work before it to report a problem with the connection
    after it."""
    from engine.db.session import release_connection

    class _Broken:
        def commit(self):
            raise RuntimeError("connection already gone")

        def rollback(self):
            raise RuntimeError("and so is the rollback")

    release_connection(_Broken())  # must not raise
