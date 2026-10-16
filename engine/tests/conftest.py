import os
import runpy
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/political_intel_test"
)

MOCK_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "mock-data"


def pytest_configure(config):
    """mock-data/fixtures/ is gitignored (generated, not checked in), so
    regenerate it from generate.py if it's missing on a fresh checkout."""
    if not (MOCK_DATA_DIR / "fixtures").exists():
        runpy.run_path(str(MOCK_DATA_DIR / "generate.py"), run_name="__main__")


ENGINE_DIR = Path(__file__).resolve().parent.parent


def _run_migrations(url: str, revision: str) -> None:
    cfg = Config(str(ENGINE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ENGINE_DIR / "alembic"))
    cfg.attributes["sqlalchemy_url"] = url
    if revision == "base":
        command.downgrade(cfg, "base")
    else:
        command.upgrade(cfg, revision)


@pytest.fixture()
def db_session():
    """A real Postgres session against a throwaway test database, since the
    models use Postgres-specific types (UUID, ARRAY, JSONB) that SQLite can't
    represent. Requires TEST_DATABASE_URL (or the local default) to be reachable.

    Schema comes from the real Alembic migrations (not create_all) so tests
    exercise exactly what production runs and catch model/migration drift.
    """
    engine = create_engine(TEST_DATABASE_URL, future=True)
    _run_migrations(TEST_DATABASE_URL, "head")
    # The SAME session semantics production uses, `expire_on_commit` included.
    #
    # This fixture built a plain sessionmaker, so every test ran with commits
    # that expire instances while production was about to stop doing that —
    # and a fixture whose sessions behave differently from the real ones
    # cannot catch the bug that difference causes. It did not: a live run
    # died because reading `ingestion_run.stats` after a commit silently
    # re-SELECTed the row on a connection that had been idle through the
    # whole model phase.
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                                expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        _run_migrations(TEST_DATABASE_URL, "base")
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        engine.dispose()


@pytest.fixture(autouse=True)
def _no_remembered_refusals():
    """A host one test marked as refusing us must not refuse the next test.

    `http.remember_refusal` is process-wide on purpose — in production it is
    what stops a blocked source being asked twelve times a run — so a test
    that exercises it would otherwise leak a block into every later test."""
    from engine.ingestion import http

    with http._PACE_LOCK:
        http._REFUSED.clear()
    yield
    with http._PACE_LOCK:
        http._REFUSED.clear()


#: Settings flags that turn a SOURCE on, as opposed to an analysis stage.
#:
#: Discovered rather than listed. Six test files each kept their own copy of
#: this list, so adding the Kenya Gazette and Kenya Law connectors left those
#: two sources running in tests that believed they had disabled everything —
#: and the symptom was not "a new source ran". It was `ingestion did not run`
#: in four end-to-end tests, because a non-empty task list suppressed the
#: mock-fixture fallback those tests depend on, three files away from
#: anything mentioning Kenya.
#:
#: A hand-maintained list of everything is a promise to update six files
#: every time one file gains a feature, and the failure it produces points
#: anywhere but at the change that caused it.
_NON_SOURCE_FLAGS = frozenset({
    "enable_verification", "enable_resolution", "enable_investigator",
    "enable_article_text", "enable_scrapling", "enable_scrapling_stealth",
})


def source_flags() -> set[str]:
    """Every `enable_*` setting that plans ingestion tasks."""
    from engine.config import settings

    return {name for name in dir(settings)
            if name.startswith("enable_") and name not in _NON_SOURCE_FLAGS}


@pytest.fixture
def no_live_sources(monkeypatch):
    """Turn every real source off, leaving the mock fixtures.

    The fixture form, so a test can ask for it by name instead of copying
    the loop again.
    """
    from engine.config import settings

    for flag in source_flags():
        monkeypatch.setattr(settings, flag, False, raising=False)
    monkeypatch.setattr(settings, "socialcrawl_api_key", "", raising=False)
    monkeypatch.setattr(settings, "newsapi_key", "", raising=False)
