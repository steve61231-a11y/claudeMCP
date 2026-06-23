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
        "url": "https://tell.co.ke/tag/presidential-immunity/",
        "domain": "tell.co.ke",
        "page_types": ["cms", "blogs", "organization"],
        "snippet": "Sifuna addresses the press in Nairobi.",
        "fetch_time": "2026-06-20 10:00:00 +00:00",
        "date_published": None,
        "_raw": {"content_info": {"author": "Tell Media", "social_metrics": None}},
    }
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"success": True, "data": {"recent_mentions": [mention]}}),
    )

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector.fetch("Edwin Sifuna", ["Sifuna"], datetime(2026, 6, 1), datetime(2026, 6, 22))

    assert len(mentions) == 1
    result = mentions[0]
    assert result["platform"] == "tell.co.ke"
    assert result["source_type"] == "blog_post"
    assert result["author_handle"] == "Tell Media"
    assert result["text"] == "Sifuna addresses the press in Nairobi."
    assert result["posted_at"] == datetime(2026, 6, 20, 10, 0, 0)
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
    mention = {"text": "A mention with minimal fields.", "date_published": "2026-06-15T00:00:00Z"}
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"success": True, "data": {"recent_mentions": [mention]}}),
    )

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector.fetch("Edwin Sifuna", [], datetime(2026, 6, 1), datetime(2026, 6, 22))

    assert len(mentions) == 1
    assert mentions[0]["author_handle"] == "unknown"
    assert mentions[0]["platform"] == "web"
    assert mentions[0]["source_type"] == "web_page"
    assert mentions[0]["posted_at"] == datetime(2026, 6, 15, 0, 0, 0)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.setattr("engine.ingestion.socialcrawl_connector.settings.socialcrawl_api_key", "")
    with pytest.raises(RuntimeError):
        SocialCrawlConnector(api_key="")
