"""Two silent collection failures, both invisible from the outside.

`SEARXNG_URL` was set to `zenith-searxng:8080` — exactly what Render's
dashboard shows for an internal service, and exactly what an operator pastes.
With no scheme `requests` raises InvalidSchema, the connector's blanket except
turned that into an empty result list, and the eighty-probe full-text discovery
sweep — the largest single source of depth in a report — was dead. The
diagnostic even said "check the instance is up", pointing away from the real
fault.

Separately, the shared session sent no User-Agent. Wikipedia's API policy
requires identifying the client and answers bare `python-requests` with 403, so
a subject with an obvious article contributed nothing and reported no error.
"""

import pytest

from engine.ingestion import http
from engine.ingestion.discovery_connector import DiscoveryConnector, _normalise_base_url


@pytest.mark.parametrize(
    "given,expected",
    [
        ("zenith-searxng:8080", "http://zenith-searxng:8080"),
        ("http://zenith-searxng:8080", "http://zenith-searxng:8080"),
        ("https://searx.example.org/", "https://searx.example.org"),
        ("  zenith-searxng:8080/  ", "http://zenith-searxng:8080"),
        ("", ""),
        (None, ""),
    ],
)
def test_a_schemeless_host_is_still_usable(given, expected):
    assert _normalise_base_url(given) == expected


def test_the_url_requests_would_refuse_is_repaired():
    """The exact value that was in production."""
    connector = DiscoveryConnector(base_url="zenith-searxng:8080")
    assert connector.base_url.startswith("http://")


def test_why_discovery_found_nothing_is_recorded(monkeypatch):
    """An empty result and a broken config were indistinguishable, which is how
    this survived. The reason now travels with the result."""
    connector = DiscoveryConnector(base_url="http://searx.invalid")

    def boom(*a, **k):
        raise ConnectionError("nope")

    monkeypatch.setattr("engine.ingestion.discovery_connector.http.get", boom)
    assert connector._search("anything") == []
    assert connector.last_error and "ConnectionError" in connector.last_error


def test_a_successful_search_clears_the_recorded_reason(monkeypatch):
    connector = DiscoveryConnector(base_url="http://searx.example")
    connector.last_error = "stale"

    class _Resp:
        status_code = 200

        def json(self):
            return {"results": [{"url": "http://a/1", "title": "t"}]}

    monkeypatch.setattr("engine.ingestion.discovery_connector.http.get", lambda *a, **k: _Resp())
    assert connector._search("q")
    assert connector.last_error is None


def test_the_shared_session_identifies_itself():
    """Wikipedia returns 403 to a client that does not."""
    session = http.build_session()
    agent = session.headers.get("User-Agent", "")
    assert "Muugi" in agent
    # ASCII on purpose: headers are latin-1, and "Mũũgĩ" cannot be encoded in
    # it — the accented form would raise before the request is sent.
    agent.encode("latin-1")
    assert agent != "python-requests"
    assert "+http" in agent, "Wikipedia's policy asks for contact information"
