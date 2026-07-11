from datetime import datetime

from engine.config import settings
from engine.ingestion import article_text as at
from engine.ingestion import wikipedia_connector as wc
from engine.ingestion.base import IngestedMention

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59))


def _article(url="https://news.example.com/story", text="Headline only"):
    return IngestedMention(
        platform="news",
        source_type="article",
        author_handle="news.example.com",
        text=text,
        posted_at=datetime(2026, 6, 10),
        engagement={},
        raw_payload={"url": url, "title": text, "source": "gdelt"},
    )


# --- article text extraction -------------------------------------------------

def test_enrich_appends_full_body_and_records_provenance(monkeypatch):
    monkeypatch.setattr(settings, "enable_article_text", True, raising=False)
    monkeypatch.setattr(at, "extract_body", lambda url, max_chars: "The full article body with lots of detail.")
    m = _article()
    n = at.enrich_with_article_text([m])
    assert n == 1
    assert "full article body" in m["text"]
    assert m["text"].startswith("Headline only")  # title preserved
    assert m["raw_payload"]["article_text"] == "The full article body with lots of detail."


def test_enrich_skips_non_articles_and_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "enable_article_text", True, raising=False)
    monkeypatch.setattr(settings, "article_text_max_fetch", 2, raising=False)
    calls = []
    monkeypatch.setattr(at, "extract_body", lambda url, mc: calls.append(url) or "body")
    posts = [
        IngestedMention(platform="tiktok", source_type="post", author_handle="u",
                        text="not an article", posted_at=datetime(2026, 6, 10),
                        engagement={}, raw_payload={"url": "https://x/y"}),
    ]
    articles = [_article(url=f"https://news/{i}") for i in range(5)]
    at.enrich_with_article_text(posts + articles)
    # Only articles fetched, and capped at article_text_max_fetch.
    assert len(calls) == 2


def test_enrich_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_article_text", False, raising=False)
    m = _article()
    assert at.enrich_with_article_text([m]) == 0
    assert "article_text" not in m["raw_payload"]


def test_extract_body_never_raises_on_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(at.http, "get", boom)
    assert at.extract_body("https://x/y", 6000) == ""


# --- wikipedia connector -----------------------------------------------------

def test_wikipedia_maps_subject_and_linked_entities(monkeypatch):
    monkeypatch.setattr(settings, "enable_wikipedia", True, raising=False)
    conn = wc.WikipediaConnector()
    monkeypatch.setattr(conn, "_resolve_title", lambda name, aliases: "John Mbadi")
    monkeypatch.setattr(conn, "_fetch_page", lambda title: ("John Mbadi is the Treasury CS.", ["National Treasury", "ODM"]))
    monkeypatch.setattr(conn, "_fetch_summary", lambda title: f"Summary of {title}.")

    out = conn.fetch("John Mbadi", ["Mbadi"], *WINDOW)
    assert out[0]["platform"] == "wikipedia"
    assert out[0]["source_type"] == "reference"
    assert out[0]["raw_payload"]["relation"] == "subject"
    relations = {m["raw_payload"]["relation"] for m in out}
    assert "linked_entity" in relations
    assert any("National Treasury" in m["text"] for m in out)


def test_wikipedia_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "enable_wikipedia", False, raising=False)
    assert wc.WikipediaConnector().fetch("X", [], *WINDOW) == []
