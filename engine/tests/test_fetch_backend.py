"""Tiering, block detection and graceful degradation for `fetch_backend`.

No test here touches the network: tier 1 is patched at `http.get` and the
scrapling tiers at their module-level entry points.
"""

import pytest

from engine.config import settings
from engine.ingestion import fetch_backend as fb


class FakeResponse:
    def __init__(self, status_code=200, text="<html>" + "body " * 3000 + "</html>"):
        self.status_code = status_code
        self.text = text


class FakePage:
    def __init__(self, status=200, html="<html>" + "body " * 3000 + "</html>"):
        self.status = status
        self.html_content = html


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    fb.reset()
    monkeypatch.setattr(settings, "enable_scrapling", True, raising=False)
    monkeypatch.setattr(settings, "enable_scrapling_stealth", False, raising=False)
    yield
    fb.reset()


# --- block detection ---------------------------------------------------------

def test_403_is_blocked():
    assert fb.looks_blocked(403, "<html>anything</html>")


def test_cloudflare_interstitial_served_as_200_is_blocked():
    assert fb.looks_blocked(200, "<html><title>Just a moment...</title></html>")


def test_long_article_mentioning_cloudflare_is_not_blocked():
    # A story *about* a Cloudflare outage must not be mistaken for a challenge
    # page — that would send every such article to the expensive tier.
    body = "<html>" + ("Cloudflare outage explained. " * 900) + "</html>"
    assert len(body) > fb._CHALLENGE_MAX_CHARS
    assert not fb.looks_blocked(200, body)


def test_empty_body_is_blocked():
    assert fb.looks_blocked(200, "")


# --- tiering -----------------------------------------------------------------

def test_plain_requests_wins_when_not_blocked(monkeypatch):
    monkeypatch.setattr(fb.http, "get", lambda url, **kw: FakeResponse())
    monkeypatch.setattr(fb, "_scrapling_fetcher", lambda: pytest.fail("tier 2 must not run"))
    result = fb.fetch_html("https://news.example/story")
    assert result.ok and result.backend == fb.BACKEND_REQUESTS
    assert fb.snapshot() == {fb.BACKEND_REQUESTS: 1}


def test_403_escalates_to_scrapling(monkeypatch):
    monkeypatch.setattr(fb.http, "get", lambda url, **kw: FakeResponse(status_code=403, text="denied"))

    class Fake:
        @staticmethod
        def get(url, **kw):
            return FakePage()

    monkeypatch.setattr(fb, "_scrapling_fetcher", lambda: Fake)
    result = fb.fetch_html("https://nation.africa/story")
    assert result.ok and result.backend == fb.BACKEND_SCRAPLING
    assert fb.snapshot() == {fb.BACKEND_SCRAPLING: 1}


def test_scrapling_is_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_scrapling", False, raising=False)
    monkeypatch.setattr(fb.http, "get", lambda url, **kw: FakeResponse(status_code=403, text="denied"))
    monkeypatch.setattr(fb, "_scrapling_fetcher", lambda: pytest.fail("tier 2 is disabled"))
    result = fb.fetch_html("https://nation.africa/story")
    assert not result.ok
    assert fb.snapshot() == {"blocked": 1}


def test_missing_scrapling_degrades_instead_of_raising(monkeypatch):
    # The wheel absent from a deploy must cost us bodies, never the run.
    monkeypatch.setattr(fb.http, "get", lambda url, **kw: FakeResponse(status_code=403, text="denied"))

    def no_module():
        raise ModuleNotFoundError("No module named 'curl_cffi'")

    monkeypatch.setattr(fb, "_scrapling_fetcher", no_module)
    result = fb.fetch_html("https://nation.africa/story")
    assert result.html == "" and result.blocked
    assert fb.snapshot() == {"blocked": 1}


def test_requests_exception_does_not_stop_escalation(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("connection reset")

    class Fake:
        @staticmethod
        def get(url, **kw):
            return FakePage()

    monkeypatch.setattr(fb.http, "get", boom)
    monkeypatch.setattr(fb, "_scrapling_fetcher", lambda: Fake)
    assert fb.fetch_html("https://nation.africa/story").backend == fb.BACKEND_SCRAPLING


def test_blocked_result_keeps_the_status_for_diagnosis(monkeypatch):
    # A bot wall and a dead link must not look the same to the caller.
    monkeypatch.setattr(settings, "enable_scrapling", False, raising=False)
    monkeypatch.setattr(fb.http, "get", lambda url, **kw: FakeResponse(status_code=429, text=""))
    result = fb.fetch_html("https://nation.africa/story")
    assert result.status == 429 and result.blocked


def test_availability_reports_config_and_counts(monkeypatch):
    monkeypatch.setattr(fb.http, "get", lambda url, **kw: FakeResponse())
    fb.fetch_html("https://news.example/a")
    info = fb.availability()
    assert info["scrapling_enabled"] is True
    assert info["pages_by_backend"] == {fb.BACKEND_REQUESTS: 1}
