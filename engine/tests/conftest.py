import os
import runpy
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.db.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/political_intel_test"
)

MOCK_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "mock-data"


def pytest_configure(config):
    """mock-data/fixtures/ is gitignored (generated, not checked in), so
    regenerate it from generate.py if it's missing on a fresh checkout."""
    if not (MOCK_DATA_DIR / "fixtures").exists():
        runpy.run_path(str(MOCK_DATA_DIR / "generate.py"), run_name="__main__")


@pytest.fixture()
def db_session():
    """A real Postgres session against a throwaway test database, since the
    models use Postgres-specific types (UUID, ARRAY, JSONB) that SQLite can't
    represent. Requires TEST_DATABASE_URL (or the local default) to be reachable.
    """
    engine = create_engine(TEST_DATABASE_URL, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
