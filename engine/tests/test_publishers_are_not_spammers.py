"""A publisher is not a spam account.

The burst rule flags an author posting more than eight times in one batch —
sound for social media, catastrophic for news. `author_handle` on an article is
the OUTLET, so nation.africa publishing sixteen stories about a major
politician looked exactly like a flood bot and every one was discarded.

A run that fetched 100 items stored 2. Nothing anywhere said so, the report was
built on almost nothing, and it looked like a model problem. No amount of
prompt or model work downstream could have rescued it.
"""

from datetime import datetime

from engine.processing import cleaning

WHEN = datetime(2026, 6, 1)


def _item(i, source_type, author, text=None, platform=None):
    return {
        "platform": platform or author,
        "source_type": source_type,
        "author_handle": author,
        "text": text or f"Rigathi Gachagua story number {i} about Mt Kenya politics",
        "posted_at": WHEN,
        "engagement": {},
        "raw_payload": {},
    }


def _kept(items):
    return [m for m in cleaning.clean_mentions(items) if not m["is_spam"]]


def test_an_outlet_publishing_many_articles_is_kept():
    """The exact production shape: five Kenyan outlets, eighty articles."""
    outlets = ["nation.africa", "standardmedia.co.ke", "the-star.co.ke",
               "citizen.digital", "tuko.co.ke"]
    articles = [_item(i, "article", outlets[i % len(outlets)]) for i in range(80)]
    assert len(_kept(articles)) == 80


def test_a_youtube_channel_with_many_videos_is_kept():
    videos = [_item(i, "video", "NTV Kenya", platform="youtube") for i in range(20)]
    assert len(_kept(videos)) == 20


def test_wikipedia_reference_entries_are_kept():
    refs = [_item(i, "reference", "wikipedia", platform="wikipedia") for i in range(12)]
    assert len(_kept(refs)) == 12


def test_a_social_account_flooding_the_feed_is_still_caught():
    """The rule must keep doing its actual job."""
    flood = [_item(i, "post", "botaccount", text=f"buy followers now offer {i}")
             for i in range(20)]
    assert _kept(flood) == []


def test_comments_from_one_person_are_still_burst_checked():
    flood = [_item(i, "comment", "same_person", text=f"first! number {i}") for i in range(20)]
    assert _kept(flood) == []


def test_a_normal_amount_of_social_posting_survives():
    """Below the threshold, an ordinary account is untouched."""
    posts = [_item(i, "post", "ordinary_person", text=f"my view on the politics {i}")
             for i in range(5)]
    assert len(_kept(posts)) == 5


def test_link_spam_is_caught_whatever_the_source_type():
    """Publisher status excuses volume, not a wall of links."""
    linky = _item(1, "article", "nation.africa",
                  text="http://a.com http://b.com http://c.com buy")
    kept = _kept([linky])
    assert kept == []


def test_rejected_items_are_flagged_not_dropped():
    """`is_spam` exists so a rejection stays countable. Discarding in memory is
    how the loss went unnoticed."""
    flood = [_item(i, "post", "botaccount", text=f"buy followers {i}") for i in range(20)]
    cleaned = cleaning.clean_mentions(flood)
    assert len(cleaned) == 20, "rejected items must still be returned"
    assert all(m["is_spam"] for m in cleaned)
