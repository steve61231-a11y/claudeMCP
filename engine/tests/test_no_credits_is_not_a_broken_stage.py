""""Some sections could not be produced" for an account with no credits.

A live report showed:

    transcripts — stopped after 3 consecutive failures:
    transcript youtube/xMSzzfV4gs8: HTTP 402
    {"type":"INSUFFICIENT_CREDITS","message":"Your account has 0 credits…"}

Nothing was broken. SOCIALCRAWL_API_KEY is set, the account behind it has no
credits, and the endpoint therefore cannot work until it is topped up or the
variable is removed. The operator read it as a fault in the engine and went
looking for a bug that did not exist — and separately, it was not obvious
from the message that SocialCrawl was the service involved at all.

An exhausted account is a configuration fact. A stage that failed and a
stage that cannot work under the current configuration are different
findings with different remedies, which is this repository's standing rule
about telling a failure apart from an absence.
"""

import pytest

from engine.ingestion.socialcrawl_connector import SocialCrawlConnector


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        import json
        return json.loads(self.text)


def _connector(monkeypatch, response):
    from engine.ingestion import http
    c = SocialCrawlConnector(api_key="k")
    monkeypatch.setattr(http, "get", lambda *a, **kw: response)
    return c


def test_the_flag_is_meaningful_before_any_request():
    """Same contract as `last_error`: a reader of the attribute must get a
    real answer without having to know whether a call has happened."""
    assert SocialCrawlConnector.credits_exhausted is False


def test_no_credits_names_the_service_and_the_variable(monkeypatch):
    c = _connector(monkeypatch, _Resp(402, '{"success":false,"error":'
                                           '{"type":"INSUFFICIENT_CREDITS",'
                                           '"message":"Your account has 0 credits"}}'))
    assert c.fetch_transcript("youtube", "xMSzzfV4gs8") is None
    assert c.credits_exhausted is True
    assert "SocialCrawl" in c.last_error, (
        "the operator cannot tell which service is out of credits")
    assert "SOCIALCRAWL_API_KEY" in c.last_error, (
        "the message does not name the variable to change")
    assert "top the account up" in c.last_error
    assert "remove the variable" in c.last_error, (
        "turning the stage off cleanly is not offered, so the only visible "
        "options are pay or keep seeing the error")


def test_it_says_nothing_else_depends_on_it(monkeypatch):
    """The operator's real question is whether the report is compromised."""
    c = _connector(monkeypatch, _Resp(402, '{"error":{"type":"INSUFFICIENT_CREDITS"}}'))
    c.fetch_transcript("youtube", "abc")
    assert "Nothing else in the report depends on it" in c.last_error


def test_an_ordinary_failure_is_still_reported_as_one(monkeypatch):
    """The credits message must not swallow a genuine fault — a 400 from a
    wrong parameter name is a bug and has to keep looking like one."""
    c = _connector(monkeypatch, _Resp(400, '{"error":"unknown parameter"}'))
    assert c.fetch_transcript("youtube", "abc") is None
    assert c.credits_exhausted is False
    assert "HTTP 400" in c.last_error
    assert "credits" not in c.last_error.lower()


def test_the_stage_only_runs_when_a_key_is_set():
    """Worth pinning, because it is the fact that answers "why is this
    running at all?" — the stage is gated on the variable existing, not on
    the account behind it working."""
    from pathlib import Path
    pipeline = (Path(__file__).resolve().parents[1] / "pipeline.py").read_text()
    assert "if settings.socialcrawl_api_key and settings.transcripts_per_run > 0:" in pipeline
