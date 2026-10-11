from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.config import settings

# Serverless Postgres (Neon) drops connections that go idle — which happens
# during the long LLM phase of a report, causing "SSL connection has been closed
# unexpectedly". Defences:
#  - pool_pre_ping: validate/replace a dead connection at checkout.
#  - pool_recycle: proactively retire connections before Neon's idle window.
#  - TCP keepalives (connect_args): keep a connection held across long LLM calls
#    alive at the socket level so it isn't reaped mid-report.
engine = create_engine(
    settings.resolved_database_url(),
    future=True,
    pool_pre_ping=True,
    pool_recycle=180,
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
)
# `expire_on_commit=False` is the one that stopped a 400-second run dying at
# the end.
#
# By default a commit EXPIRES every instance in the session, so the next read
# of any attribute silently re-SELECTs the whole row. The report pipeline
# loads `ingestion_run` during collection, commits, spends minutes in the
# model phase, and then reads `ingestion_run.status` and `.stats` to build the
# provenance block. That read emitted
#
#     SELECT ingestion_runs.id, ..., ingestion_runs.credits_spent, ...
#
# on a pooled connection that had been idle throughout the model phase. Neon
# had long since closed it, and the run failed with "SSL connection has been
# closed unexpectedly" — after the collection, the digest and every analyst
# had already finished and been paid for.
#
# `pool_pre_ping` cannot help there: it validates a connection at CHECKOUT,
# and an open transaction keeps the same one held for the whole phase.
#
# The deeper point is that the query had no reason to exist. Those values were
# already in memory and nothing else writes that row. Turning the expiry off
# removes a whole class of invisible SELECTs that appear minutes after the
# statement that caused them — which is also why the failure named a table
# nobody in the stack trace had asked about.
#
# The cost is a session that can hold a stale value if another process changes
# a row it loaded. The runs here own the rows they read, and the pipeline
# re-queries explicitly where freshness matters.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def release_connection(db) -> None:
    """Give the pooled connection back before a phase that will not use it.

    A session inside a transaction OWNS a connection. The two longest phases
    in this product — scraping, and the analyst fan-out — do no database work
    at all and take minutes, and both were entered with a read transaction
    open from the query just before. That connection then sat idle for the
    whole phase, serverless Postgres closed it, and the first statement
    afterwards failed with "SSL connection has been closed unexpectedly",
    naming a SELECT nobody had written.

    `pool_pre_ping` and `pool_recycle` cannot help: both act at CHECKOUT, and
    a held connection is never checked out again. They were correctly
    configured and looking the other way, which is worse than their absence
    because they read as proof the problem was handled.

    So the rule is about ownership rather than configuration: **no session
    holds a connection across a phase that does not use it.** Ending the
    transaction returns it to the pool; the next statement checks out a fresh
    one and pre-ping does exactly what it is for.

    Commits rather than rolls back: at these seams the preceding work is
    finished and wanted. Never raises — a connection that cannot be released
    is already broken, and the stage that notices should not be the one that
    reports it.
    """
    try:
        db.commit()
    except Exception:  # noqa: BLE001 — see above
        try:
            db.rollback()
        except Exception:
            pass
