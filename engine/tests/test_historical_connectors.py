from datetime import datetime
from types import SimpleNamespace

from engine.ingestion.gdelt_connector import GdeltConnector
from engine.ingestion.wayback_connector import WaybackConnector
from engine.ingestion.twscrape_connector import TwscrapeConnector


class FakeResp:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


# ---- GDELT -----------------------------------------------------------------

def test_gdelt_maps_historical_articles(monkeypatch):
    payload = {
        "articles": [
            {"url": "https://nation.africa/mbadi-2025", "title": "Mbadi named Treasury CS",
             "seendate": "20251215T090000Z", "domain": "nation.africa", "language": "English"},
            {"url": "https://the-star.co.ke/mbadi-budget", "title": "Mbadi tables budget",
             "seendate": "20260615T100000Z", "domain": "the-star.co.ke", "language": "English"},
            {"url": "https://nation.africa/mbadi-2025", "title": "dup", "seendate": "20251215T090000Z"},  # dup url
        ]
    }
    monkeypatch.setattr("engine.ingestion.gdelt_connector.http.get", lambda *a, **k: FakeResp(payload))
    # A December-2025 article proves the >30-day historical reach.
    mentions = GdeltConnector().fetch("Mbadi", ["John Mbadi"], datetime(2025, 12, 1), datetime(2026, 7, 1))
    urls = [m["raw_payload"]["url"] for m in mentions]
    assert urls == ["https://nation.africa/mbadi-2025", "https://the-star.co.ke/mbadi-budget"]  # deduped
    dec = next(m for m in mentions if "2025" in m["raw_payload"]["url"])
    assert dec["posted_at"].year == 2025 and dec["platform"] == "nation.africa"


def test_gdelt_empty_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network")
    monkeypatch.setattr("engine.ingestion.gdelt_connector.http.get", boom)
    assert GdeltConnector().fetch("Mbadi", [], datetime(2026, 1, 1), datetime(2026, 7, 1)) == []


# ---- Wayback ---------------------------------------------------------------

def test_wayback_cdx_maps_snapshots(monkeypatch):
    cdx = [
        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["ke,nation)/john-mbadi-defends-budget-991", "20260201120000",
         "https://nation.africa/kenya/john-mbadi-defends-budget-991", "text/html", "200", "X", "1"],
    ]
    monkeypatch.setattr("engine.ingestion.wayback_connector.http.get", lambda *a, **k: FakeResp(cdx))
    conn = WaybackConnector(domains=["nation.africa"])
    mentions = conn.fetch("John Mbadi", [], datetime(2026, 1, 1), datetime(2026, 3, 1))
    assert len(mentions) == 1
    m = mentions[0]
    assert m["text"] == "John Mbadi Defends Budget"  # title reconstructed from slug
    assert "web.archive.org" in m["raw_payload"]["archived_url"]
    assert m["posted_at"] == datetime(2026, 2, 1, 12, 0)


def test_wayback_recover_returns_text(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **k):
        calls["n"] += 1
        if "available" in url:
            return FakeResp({"archived_snapshots": {"closest": {"available": True, "url": "https://web.archive.org/web/x/live"}}})
        return FakeResp(text="<html><body><p>Mbadi said the budget protects Kenyans.</p></body></html>")

    monkeypatch.setattr("engine.ingestion.wayback_connector.http.get", fake_get)
    text = WaybackConnector().recover("https://dead.example/article")
    assert "Mbadi said the budget protects Kenyans." in text


# ---- twscrape --------------------------------------------------------------

class _FakeTweet(SimpleNamespace):
    pass


def test_twscrape_maps_tweets():
    tweets = [
        _FakeTweet(id="1", rawContent="Mbadi must explain the budget", date=datetime(2026, 6, 1),
                   likeCount=50, retweetCount=10, replyCount=5, viewCount=9000, url="https://x.com/a/1",
                   user=SimpleNamespace(username="citizen1", followersCount=3000)),
        _FakeTweet(id="1", rawContent="dup", date=datetime(2026, 6, 1), user=SimpleNamespace(username="x")),  # dup id
    ]

    class FakeAPI:
        async def search(self, query, limit=0):
            for t in tweets:
                yield t

    mentions = TwscrapeConnector(api=FakeAPI()).fetch("Mbadi", [], datetime(2026, 5, 1), datetime(2026, 7, 1))
    assert len(mentions) == 1  # deduped by id
    m = mentions[0]
    assert m["platform"] == "twitter" and m["author_handle"] == "citizen1"
    assert m["engagement"]["likes"] == 50 and m["engagement"]["shares"] == 10
    assert m["raw_payload"]["followers"] == 3000
