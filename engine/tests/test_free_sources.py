"""Tests for the keyless free connectors (Google News RSS, Reddit, YouTube) and
the run-status 'partial' fix — the data-starvation remediation."""

from datetime import datetime

from engine.config import settings
from engine.ingestion import google_news_rss_connector as gnews
from engine.ingestion import reddit_connector as reddit
from engine.ingestion import youtube_connector as yt
from engine.ingestion.base import IngestedMention

WINDOW = (datetime(2026, 1, 1), datetime(2026, 6, 30, 23, 59, 59))


class FakeResp:
    def __init__(self, *, content=b"", json_data=None, status=200):
        self.content = content
        self._json = json_data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


# --- Google News RSS ---------------------------------------------------------

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Mbadi defends budget</title><link>https://nation.africa/a1</link>
<pubDate>Wed, 10 Jun 2026 09:00:00 GMT</pubDate><source url="https://nation.africa">Nation</source></item>
<item><title>CS Mbadi on taxes</title><link>https://standardmedia.co.ke/a2</link>
<pubDate>Thu, 11 Jun 2026 10:00:00 GMT</pubDate><source url="https://standardmedia.co.ke">Standard</source></item>
</channel></rss>"""


def test_google_news_maps_items(monkeypatch):
    monkeypatch.setattr(gnews.http, "get", lambda *a, **k: FakeResp(content=RSS))
    out = gnews.GoogleNewsRssConnector().fetch("John Mbadi", ["Mbadi"], *WINDOW)
    assert len(out) == 2
    assert out[0]["source_type"] == "article"
    assert out[0]["raw_payload"]["source"] == "google_news_rss"
    assert out[0]["raw_payload"]["url"] == "https://nation.africa/a1"
    assert out[0]["posted_at"] == datetime(2026, 6, 10, 9, 0, 0)


def test_google_news_degrades(monkeypatch):
    monkeypatch.setattr(gnews.http, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no egress")))
    assert gnews.GoogleNewsRssConnector().fetch("X", [], *WINDOW) == []


# --- Reddit ------------------------------------------------------------------

REDDIT_JSON = {
    "data": {"children": [
        {"data": {"id": "p1", "title": "Thoughts on Mbadi budget", "selftext": "Solid plan",
                  "author": "wanjiku", "score": 42, "num_comments": 12,
                  "created_utc": 1780000000, "permalink": "/r/Kenya/p1", "subreddit": "Kenya"}},
        {"data": {"id": "p2", "title": "Mbadi again", "selftext": "", "author": "otieno",
                  "score": 5, "num_comments": 0, "created_utc": 1780500000,
                  "permalink": "/r/Kenya/p2", "subreddit": "Kenya"}},
    ]}
}


def test_reddit_maps_posts(monkeypatch):
    monkeypatch.setattr(reddit.http, "get", lambda *a, **k: FakeResp(json_data=REDDIT_JSON))
    out = reddit.RedditConnector().fetch("John Mbadi", ["Mbadi"], *WINDOW)
    # site-wide + r/Kenya both return the same 2 → dedupe by id → 2.
    assert len(out) == 2
    assert out[0]["platform"] == "reddit"
    assert out[0]["engagement"]["likes"] == 42
    assert out[0]["raw_payload"]["url"] == "https://www.reddit.com/r/Kenya/p1"


def test_reddit_degrades(monkeypatch):
    monkeypatch.setattr(reddit.http, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    assert reddit.RedditConnector().fetch("X", [], *WINDOW) == []


# --- YouTube -----------------------------------------------------------------

class FakeYDL:
    def __init__(self, info):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, target, download=False):
        return self._info


def test_youtube_maps_videos_and_comments(monkeypatch):
    search_info = {"entries": [
        {"id": "v1", "title": "Mbadi interview", "description": "on the budget",
         "channel": "NTV Kenya", "view_count": 10000, "upload_date": "20260610"},
    ]}
    video_info = {"comments": [
        {"text": "Great points", "author": "@viewer1", "like_count": 3},
        {"text": "Not sure about this", "author": "@viewer2", "like_count": 1},
    ]}

    def factory(opts):
        return FakeYDL(video_info if opts.get("getcomments") else search_info)

    conn = yt.YouTubeConnector(ydl_factory=factory)
    out = conn.fetch("John Mbadi", ["Mbadi"], *WINDOW)
    kinds = {m["source_type"] for m in out}
    assert "video" in kinds and "comment" in kinds
    video = next(m for m in out if m["source_type"] == "video")
    assert video["platform"] == "youtube"
    assert video["engagement"]["views"] == 10000
    assert any("Great points" in m["text"] for m in out)


def test_youtube_degrades(monkeypatch):
    def factory(opts):
        raise RuntimeError("yt-dlp blocked")
    assert yt.YouTubeConnector(ydl_factory=factory).fetch("X", [], *WINDOW) == []
