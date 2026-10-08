"""The container that died without saying why.

The entrypoint was `alembic upgrade head && uvicorn ...`. With no
DATABASE_URL — the single likeliest thing to be missing on a first deploy —
alembic exits non-zero, uvicorn never starts, the container dies, and the
host reports "1/1 service crashed" and nothing else.

That is bad everywhere and worse on a host that only offers a public domain
once a service is listening. A crash loop means the domain can never be
generated, so the operator goes hunting for a menu that cannot exist while
the actual cause — one unset variable — is a line in a log nobody told them
to open. An evening went into exactly that.

So the container starts the server whatever happens, prints the reason in
plain words, and the health endpoint tells the truth about it. A page that
loads and explains itself beats a container that vanishes.

The second half matters as much as the first. `ok: true` was hardcoded, so
an app running with no database at all would have reported itself healthy —
the same lie, moved somewhere new. This codebase exists to distinguish "this
failed" from "there was nothing here", and a health check is the one place
that distinction is read by a machine.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
START = (ROOT / "engine" / "start.sh").read_text()
DOCKERFILE = (ROOT / "engine" / "Dockerfile.render").read_text()


def test_the_entrypoint_is_not_a_chain_that_swallows_the_server():
    """`alembic && uvicorn` is the defect. If migrations fail for any reason
    the server never starts, and the only symptom is a dead container."""
    assert "alembic upgrade head && " not in DOCKERFILE, (
        "the entrypoint chains uvicorn behind alembic again — a failed "
        "migration will kill the container instead of reporting itself")
    assert "start.sh" in DOCKERFILE


def test_the_server_starts_even_when_migrations_fail():
    """The `exec uvicorn` must not sit behind a conditional."""
    lines = [l.strip() for l in START.splitlines() if l.strip().startswith("exec uvicorn")]
    assert len(lines) == 1, "expected exactly one unconditional uvicorn start"
    # It must be at the top level of the script, not nested in an if/else.
    for line in START.splitlines():
        if line.strip().startswith("exec uvicorn"):
            assert not line.startswith(" "), (
                "uvicorn is indented, so it is inside a conditional — it has to "
                "run whatever happened above it")


def test_a_missing_database_url_is_said_out_loud():
    assert "DATABASE_URL is not set" in START
    assert "neon.tech" in START, "the message should say where to get one"


def test_a_failed_migration_is_said_out_loud():
    assert "MIGRATIONS FAILED" in START


def test_the_port_has_a_default():
    """Render injects PORT, Fly and Railway may not, and a bare `--port ` with
    nothing after it fails instantly."""
    assert "${PORT:-8000}" in START


# --- the health endpoint must not claim to be fine -------------------------

def test_health_is_not_hardcoded_ok():
    source = (ROOT / "engine" / "api_server.py").read_text()
    health = source[source.index("def health():"):]
    health = health[:health.index("\n@app.")] if "\n@app." in health else health
    assert '"ok": True' not in health, (
        "health reports ok unconditionally — it would say the app is fine "
        "while running with no database at all")
    assert '"ok": database["reachable"]' in health


@pytest.fixture()
def client():
    from engine import api_server

    return TestClient(api_server.app)


def test_health_reports_a_working_database(client, monkeypatch, db_session):
    """`db_session` binds its own engine and does not replace the app's, so
    the endpoint would otherwise be reporting on whatever DATABASE_URL this
    machine happens to have. Point it at the session that is known good."""
    from engine import api_server

    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    body = client.get("/api/health").json()
    assert body["database"]["reachable"] is True
    assert body["ok"] is True
    # Nothing to explain when it works.
    assert "hint" not in body["database"]


def test_health_explains_an_unreachable_database(client, monkeypatch):
    """The operator needs to know WHICH of the two it is: no connection string
    at all, or one that does not work. The remedies are different."""
    from engine import api_server

    class _Dead:
        def execute(self, *a, **k):
            raise RuntimeError("connection refused")

        def query(self, *a, **k):
            raise RuntimeError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr(api_server, "SessionLocal", lambda: _Dead())
    body = client.get("/api/health").json()
    assert body["ok"] is False
    assert body["database"]["reachable"] is False
    assert "could not be reached" in body["database"]["hint"]
    assert "connection refused" in body["database"]["error"]
