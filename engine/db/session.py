from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.config import settings

# pool_pre_ping + pool_recycle keep connections healthy on serverless Postgres
# (Neon/Supabase) that closes idle connections — without them the first query
# after an idle period fails with "server closed the connection unexpectedly".
engine = create_engine(
    settings.resolved_database_url(),
    future=True,
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
