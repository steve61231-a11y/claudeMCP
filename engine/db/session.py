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
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
