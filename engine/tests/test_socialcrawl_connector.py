from datetime import datetime

import pytest

from engine.ingestion.socialcrawl_connector import SocialCrawlConnector


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_maps_recent_mentions_to_mentions(monkeypatch):
    mention = {
        "platform": "x",
        "source_type": "social_post",
        "author": "@kenyan_voice",
        "text": "Sifuna addresses the press in Nairobi.",
        "posted_at": "2026-06-20T10:00:00Z",
        "engagement": {"likes": 12, "shares": 3, "comments": 1},
    }
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"success": True, "data": {"recent_mentions": [mention]}}),
    )

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector.fetch("Edwin Sifuna", ["Sifuna"], datetime(2026, 6, 1), datetime(2026, 6, 22))

    assert len(mentions) == 1
    result = mentions[0]
    assert result["platform"] == "x"
    assert result["author_handle"] == "@kenyan_voice"
    assert result["posted_at"] == datetime(2026, 6, 20, 10, 0, 0)
    assert result["engagement"] == {"likes": 12, "shares": 3, "comments": 1}
    assert result["raw_payload"] == mention


def test_fetch_handles_error_envelope(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse(
            {"success": False, "error": {"type": "INVALID_API_KEY", "message": "Bad key"}}
        ),
    )

    connector = SocialCrawlConnector(api_key="sc_test-key")
    with pytest.raises(RuntimeError):
        connector.fetch("Edwin Sifuna", [], datetime(2026, 6, 1), datetime(2026, 6, 22))


def test_fetch_handles_missing_optional_fields(monkeypatch):
    mention = {"text": "A mention with minimal fields.", "date": "2026-06-15T00:00:00Z"}
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"success": True, "data": {"recent_mentions": [mention]}}),
    )

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector.fetch("Edwin Sifuna", [], datetime(2026, 6, 1), datetime(2026, 6, 22))

    assert len(mentions) == 1
    assert mentions[0]["author_handle"] == "unknown"
    assert mentions[0]["platform"] == "unknown"
    assert mentions[0]["posted_at"] == datetime(2026, 6, 15, 0, 0, 0)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.setattr("engine.ingestion.socialcrawl_connector.settings.socialcrawl_api_key", "")
    with pytest.raises(RuntimeError):
        SocialCrawlConnector(api_key="")
