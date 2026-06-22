from datetime import datetime

from engine.processing.cleaning import clean_mentions


def make_mention(author="a", text="hello world", platform="x"):
    return {
        "platform": platform,
        "source_type": "post",
        "author_handle": author,
        "text": text,
        "posted_at": datetime(2026, 1, 1),
        "engagement": {},
        "raw_payload": {},
    }


def test_dedupes_identical_mentions():
    mentions = [make_mention(), make_mention()]
    cleaned = clean_mentions(mentions)
    assert len(cleaned) == 1


def test_flags_link_heavy_text_as_spam():
    mention = make_mention(text="check this http://a.co http://b.co http://c.co now")
    cleaned = clean_mentions([mention])
    assert cleaned[0]["is_spam"] is True


def test_keeps_normal_text():
    mention = make_mention(text="the governor visited the market today")
    cleaned = clean_mentions([mention])
    assert cleaned[0]["is_spam"] is False
