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


def test_fetch_discovery_maps_native_social_results(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "tiktok/search" in url:
            return FakeResponse(
                {
                    "success": True,
                    "data": {
                        "videos": [
                            {
                                "text": "Sifuna trends on TikTok",
                                "author": "@kenyanvoices",
                                "created_at": "2026-06-18T12:00:00Z",
                                "engagement": {"likes": 500, "shares": 20},
                            }
                        ]
                    },
                }
            )
        if "linkedin/search/posts" in url:
            return FakeResponse(
                {
                    "success": True,
                    "data": {
                        "posts": [
                            {
                                "text": "Senator Sifuna addresses the Senate.",
                                "username": "james-orengo",
                                "published_at": "2026-06-19T08:00:00Z",
                            }
                        ]
                    },
                }
            )
        return FakeResponse({"success": True, "data": {}})

    monkeypatch.setattr("requests.get", fake_get)

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector._fetch_discovery(
        "Edwin Sifuna", ["Sifuna"], datetime(2026, 6, 1), datetime(2026, 6, 22)
    )

    platforms = {m["platform"] for m in mentions}
    assert "tiktok" in platforms
    assert "linkedin" in platforms
    tiktok_mention = next(m for m in mentions if m["platform"] == "tiktok")
    assert tiktok_mention["author_handle"] == "@kenyanvoices"
    assert tiktok_mention["engagement"] == {"likes": 500, "shares": 20}


def test_fetch_discovery_isolates_per_platform_failures(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "twitter/ai-search" in url:
            raise RuntimeError("rate limited")
        if "youtube/search" in url and "hashtag" not in url:
            return FakeResponse(
                {
                    "success": True,
                    "data": {
                        "videos": [
                            {
                                "title": "Sifuna interview",
                                "channel_name": "Citizen TV",
                                "timestamp": "2026-06-17T09:00:00Z",
                            }
                        ]
                    },
                }
            )
        return FakeResponse({"success": False, "error": {"type": "INSUFFICIENT_CREDITS"}})

    monkeypatch.setattr("requests.get", fake_get)

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector._fetch_discovery(
        "Edwin Sifuna", [], datetime(2026, 6, 1), datetime(2026, 6, 22)
    )

    assert len(mentions) == 1
    assert mentions[0]["platform"] == "youtube"
    assert mentions[0]["author_handle"] == "Citizen TV"


def test_fetch_profile_activity_maps_handle_based_results(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "tiktok/profile/videos" in url:
            return FakeResponse(
                {
                    "success": True,
                    "data": {
                        "videos": [
                            {
                                "text": "Official update from Sifuna.",
                                "created_at": "2026-06-20T10:00:00Z",
                                "engagement": {"likes": 1200},
                            }
                        ]
                    },
                }
            )
        return FakeResponse({"success": True, "data": {}})

    monkeypatch.setattr("requests.get", fake_get)

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector.fetch_profile_activity(
        {"tiktok": "@edwinsifuna_official"},
        datetime(2026, 6, 1),
        datetime(2026, 6, 22),
        source_type="post",
    )

    assert len(mentions) == 1
    assert mentions[0]["platform"] == "tiktok"
    assert mentions[0]["author_handle"] == "@edwinsifuna_official"
    assert mentions[0]["engagement"] == {"likes": 1200}


def test_fetch_profile_activity_skips_missing_handles(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse({"success": True, "data": {}}))

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector.fetch_profile_activity(
        {"tiktok": "", "facebook": None}, datetime(2026, 6, 1), datetime(2026, 6, 22)
    )

    assert mentions == []


def test_fetch_profile_activity_isolates_per_platform_failures(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "youtube/channel/videos" in url:
            raise RuntimeError("network error")
        return FakeResponse(
            {"success": True, "data": {"posts": [{"text": "Rival post", "username": "rival_handle"}]}}
        )

    monkeypatch.setattr("requests.get", fake_get)

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector.fetch_profile_activity(
        {"youtube": "rivalchannel", "linkedin": "rival-co"},
        datetime(2026, 6, 1),
        datetime(2026, 6, 22),
        source_type="rival_activity",
    )

    assert len(mentions) == 1
    assert mentions[0]["platform"] == "linkedin"
    assert mentions[0]["source_type"] == "rival_activity"


def test_fetch_discovery_pulls_comments_for_posts_with_ids(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "tiktok/search/hashtag" in url:
            return FakeResponse({"success": True, "data": {}})
        if "tiktok/search" in url:
            return FakeResponse(
                {
                    "success": True,
                    "data": {
                        "videos": [
                            {"id": "vid123", "text": "Sifuna on TikTok", "author": "@user1"}
                        ]
                    },
                }
            )
        if "tiktok/post/comments" in url:
            assert params["id"] == "vid123"
            return FakeResponse(
                {"success": True, "data": {"comments": [{"text": "So true!", "username": "fan1"}]}}
            )
        return FakeResponse({"success": True, "data": {}})

    monkeypatch.setattr("requests.get", fake_get)

    connector = SocialCrawlConnector(api_key="sc_test-key")
    mentions = connector._fetch_discovery(
        "Edwin Sifuna", [], datetime(2026, 6, 1), datetime(2026, 6, 22)
    )

    comment_mentions = [m for m in mentions if m["source_type"] == "comment"]
    assert len(comment_mentions) == 1
    assert comment_mentions[0]["author_handle"] == "fan1"
    assert comment_mentions[0]["platform"] == "tiktok"


def test_discover_handles_returns_candidates_per_platform(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "tiktok/search/users" in url:
            return FakeResponse(
                {"success": True, "data": {"users": [{"username": "@edwinsifuna_official"}]}}
            )
        if "youtube/search" in url:
            return FakeResponse(
                {"success": True, "data": {"items": [{"channel_name": "Edwin Sifuna"}]}}
            )
        return FakeResponse({"success": True, "data": {}})

    monkeypatch.setattr("requests.get", fake_get)

    connector = SocialCrawlConnector(api_key="sc_test-key")
    candidates = connector.discover_handles("Edwin Sifuna")

    assert candidates["tiktok"] == ["@edwinsifuna_official"]
    assert candidates["youtube"] == ["Edwin Sifuna"]


def test_discover_handles_isolates_platform_failures(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "tiktok/search/users" in url:
            raise RuntimeError("rate limited")
        return FakeResponse({"success": True, "data": {"items": [{"channel_name": "Edwin Sifuna"}]}})

    monkeypatch.setattr("requests.get", fake_get)

    connector = SocialCrawlConnector(api_key="sc_test-key")
    candidates = connector.discover_handles("Edwin Sifuna")

    assert candidates["tiktok"] == []
    assert candidates["youtube"] == ["Edwin Sifuna"]
