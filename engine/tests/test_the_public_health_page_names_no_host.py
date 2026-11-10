"""/api/health is public, so it must not name the client's database.

A live check of the deployment on 10 Nov 2026 found the health report
returning `database.host` — the full Neon endpoint hostname — to anyone who
asked. The endpoint needs no key by design (it is how an operator, a monitor
and a deploy workflow ask "is it up"), which is exactly why it may report
only whether things work and never anything that identifies where the
client's data lives. It now says what kind of database it is.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_the_health_report_does_not_contain_the_database_hostname(monkeypatch):
    from engine import api_server
    from engine.db.session import engine

    client = TestClient(api_server.app)
    body = client.get("/api/health").json()
    database = body["database"]
    assert "host" not in database
    assert database["provider"] in ("neon", "supabase", "local", "other")
    host = engine.url.host
    # A real hostname has dots; a bare local name like "postgres" appears in
    # unrelated words and identifies nothing.
    if host and "." in host:
        assert host not in json.dumps(body), "the database hostname is in the public health report"


def test_a_neon_host_is_labelled_without_being_named(monkeypatch):
    from sqlalchemy.engine import make_url

    from engine import api_server
    from engine.db import session as session_mod

    class Bound:
        url = make_url("postgresql://u:p@ep-quiet-name-pooler.c-6.us-east-2.aws.neon.tech/db")

    monkeypatch.setattr(session_mod, "engine", Bound)
    body = TestClient(api_server.app).get("/api/health").json()
    assert body["database"]["provider"] == "neon"
    assert "ep-quiet-name" not in json.dumps(body)
