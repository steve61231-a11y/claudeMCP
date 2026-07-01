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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
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
