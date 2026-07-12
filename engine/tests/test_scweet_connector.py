from datetime import datetime

from engine.config import settings
from engine.ingestion.scweet_connector import ScweetConnector

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59))


class FakeScweet:
    """Stand-in for a Scweet instance: returns fixture tweet rows."""

    def __init__(self, rows, boom=False):
        self._rows = rows
        self._boom = boom
        self.calls = []

    def scrape(self, words, since, until, limit):
        self.calls.append((tuple(words), since, until, limit))
        if self._boom:
            raise RuntimeError("scweet backend down")
        return self._rows


def _rows():
    return [
        {"tweetId": "111", "UserScreenName": "@wanjiku", "Timestamp": "2026-06-10 09:00:00",
         "Text": "Mbadi budget speech was solid", "Likes": "12", "Retweets": "3", "Comments": "1",
         "TweetURL": "https://x.com/wanjiku/status/111"},
        {"tweetId": "222", "UserScreenName": "otieno", "Timestamp": "2026-06-11",
         "Embedded_text": "Not convinced by the numbers", "Likes": "5"},
    ]


def test_scweet_maps_rows_to_mentions(monkeypatch):
    monkeypatch.setattr(settings, "enable_scweet", True, raising=False)
    conn = ScweetConnector(scraper=FakeScweet(_rows()))
    out = conn.fetch("John Mbadi", ["Mbadi"], *WINDOW)

    assert len(out) == 2
    m = out[0]
    assert m["platform"] == "x" and m["source_type"] == "post"
    assert m["author_handle"] == "wanjiku"  # leading @ stripped
    assert m["engagement"] == {"likes": 12, "shares": 3, "comments": 1}
    assert m["raw_payload"]["source"] == "scweet"
    assert m["posted_at"] == datetime(2026, 6, 10, 9, 0, 0)
    # Embedded_text fallback works for the second row.
    assert "Not convinced" in out[1]["text"]


def test_scweet_dedupes_across_terms(monkeypatch):
    monkeypatch.setattr(settings, "enable_scweet", True, raising=False)
    conn = ScweetConnector(scraper=FakeScweet(_rows()))
    out = conn.fetch("John Mbadi", ["Mbadi", "CS Mbadi"], *WINDOW)
    # 3 terms each return the same 2 rows, but tweetId dedupe keeps 2.
    assert len(out) == 2


def test_scweet_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "enable_scweet", False, raising=False)
    conn = ScweetConnector(scraper=FakeScweet(_rows()))
    assert conn.fetch("X", [], *WINDOW) == []


def test_scweet_degrades_on_backend_error(monkeypatch):
    monkeypatch.setattr(settings, "enable_scweet", True, raising=False)
    conn = ScweetConnector(scraper=FakeScweet([], boom=True))
    assert conn.fetch("X", [], *WINDOW) == []


def test_scweet_no_scraper_without_creds(monkeypatch):
    # Enabled but no injected scraper and no creds → build returns None → [].
    monkeypatch.setattr(settings, "enable_scweet", True, raising=False)
    monkeypatch.setattr(settings, "x_username", "", raising=False)
    monkeypatch.setattr(settings, "x_password", "", raising=False)
    assert ScweetConnector().fetch("X", [], *WINDOW) == []
