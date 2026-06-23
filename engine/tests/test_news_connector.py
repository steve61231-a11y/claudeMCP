from datetime import datetime

import pytest

from engine.ingestion.news_connector import NewsApiConnector


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_maps_articles_to_mentions(monkeypatch):
    article = {
        "title": "Senator addresses crowd",
        "description": "Speech in Nairobi",
        "content": "Full text of the speech.",
        "publishedAt": "2026-06-20T10:00:00Z",
        "source": {"name": "Daily Nation"},
    }
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse({"articles": [article], "totalResults": 1}),
    )

    connector = NewsApiConnector(api_key="test-key")
    mentions = connector.fetch("Jane Doe", ["Senator Doe"], datetime(2026, 6, 1), datetime(2026, 6, 22))

    assert len(mentions) == 1
    mention = mentions[0]
    assert mention["platform"] == "news"
    assert mention["author_handle"] == "Daily Nation"
    assert mention["posted_at"] == datetime(2026, 6, 20, 10, 0, 0)
    assert "Senator addresses crowd" in mention["text"]
    assert mention["raw_payload"] == article


def test_fetch_paginates_until_total_results_reached(monkeypatch):
    pages = [
        {"articles": [{"title": "A", "publishedAt": "2026-06-20T10:00:00Z", "source": {}}], "totalResults": 150},
        {"articles": [{"title": "B", "publishedAt": "2026-06-21T10:00:00Z", "source": {}}], "totalResults": 150},
    ]
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(kwargs["params"]["page"])
        return FakeResponse(pages[len(calls) - 1])

    monkeypatch.setattr("requests.get", fake_get)

    connector = NewsApiConnector(api_key="test-key")
    mentions = connector.fetch("Jane Doe", [], datetime(2026, 6, 1), datetime(2026, 6, 22))

    assert calls == [1, 2]
    assert len(mentions) == 2


def test_missing_api_key_raises():
    with pytest.raises(RuntimeError):
        NewsApiConnector(api_key="")
