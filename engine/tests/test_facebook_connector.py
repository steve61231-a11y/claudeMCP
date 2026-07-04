from datetime import datetime

from engine.ingestion.facebook_connector import FacebookConnector

# A fixture matching facebook-scraper's real get_posts() dict output.
POSTS = [
    {
        "post_id": "111",
        "post_url": "https://facebook.com/CitizenTVKe/posts/111",
        "text": "Treasury CS John Mbadi defends the 4.8 trillion budget before Parliament.",
        "time": datetime(2026, 6, 15, 10, 0),
        "username": "Citizen TV Kenya",
        "likes": 1200,
        "comments": 340,
        "shares": 88,
        "reactions": {"like": 1000, "angry": 200},
        "comments_full": [
            {"comment_text": "Mbadi is just protecting Ruto's mess", "commenter_name": "Otieno K", "comment_reactions_count": 40},
            {"comment_text": "Great explanation, finally some clarity", "commenter_name": "Wanjiku M", "comment_reactions_count": 12},
        ],
    },
    {
        # post NOT about the politician, but a comment names him -> comment kept
        "post_id": "222",
        "post_url": "https://facebook.com/CitizenTVKe/posts/222",
        "text": "Weather update for Nairobi today.",
        "time": datetime(2026, 6, 15, 11, 0),
        "username": "Citizen TV Kenya",
        "comments_full": [
            {"comment_text": "Off topic but Mbadi should fix fuel prices", "commenter_name": "Juma", "comment_reactions_count": 3},
            {"comment_text": "Nice weather", "commenter_name": "Ann", "comment_reactions_count": 0},
        ],
    },
]


def _scraper(**kwargs):
    # Only the first configured page yields our fixture; others empty.
    return POSTS if kwargs.get("account") == "CitizenTVKe" else []


def test_maps_posts_and_comments():
    conn = FacebookConnector(pages=["CitizenTVKe"], scraper=_scraper)
    mentions = conn.fetch("Mbadi", ["John Mbadi"], datetime(2026, 6, 1), datetime(2026, 7, 1))

    posts = [m for m in mentions if m["source_type"] == "post"]
    comments = [m for m in mentions if m["source_type"] == "comment"]
    # One relevant post (111); post 222 is off-topic so no post mention.
    assert len(posts) == 1 and posts[0]["raw_payload"]["post_id"] == "111"
    assert posts[0]["platform"] == "facebook"
    assert posts[0]["engagement"]["comments"] == 340
    # Comments: both from post 111 (post is relevant) + the Mbadi-naming comment on 222.
    texts = [c["text"] for c in comments]
    assert "Mbadi is just protecting Ruto's mess" in texts
    assert "Off topic but Mbadi should fix fuel prices" in texts
    assert "Nice weather" not in texts  # off-topic comment on off-topic post dropped
    # Comments carry parent-post linkage for the comment-relevance rule.
    linked = [c for c in comments if c["raw_payload"].get("_parent_post")]
    assert linked and linked[0]["raw_payload"]["_parent_post"]["id"] in ("111", "222")


def test_window_filter_and_dedupe():
    old_post = dict(POSTS[0], post_id="333", time=datetime(2020, 1, 1))

    def scraper(**kwargs):
        return [POSTS[0], POSTS[0], old_post] if kwargs.get("account") == "CitizenTVKe" else []

    conn = FacebookConnector(pages=["CitizenTVKe"], scraper=scraper)
    mentions = conn.fetch("Mbadi", [], datetime(2026, 6, 1), datetime(2026, 7, 1))
    post_ids = {m["raw_payload"].get("post_id") for m in mentions if m["source_type"] == "post"}
    assert post_ids == {"111"}  # duplicate collapsed, 2020 post outside window dropped


def test_page_failure_is_isolated():
    def scraper(**kwargs):
        if kwargs.get("account") == "broken":
            raise RuntimeError("page blocked")
        return POSTS if kwargs.get("account") == "CitizenTVKe" else []

    conn = FacebookConnector(pages=["broken", "CitizenTVKe"], scraper=scraper)
    mentions = conn.fetch("Mbadi", [], datetime(2026, 6, 1), datetime(2026, 7, 1))
    assert any(m["raw_payload"].get("post_id") == "111" for m in mentions)  # good page still ingested
