"""Web discovery via SearXNG metasearch.

Discovery is how material that no fixed connector indexes — archived pages, old
filings, obscure outlets — enters the corpus. These tests pin the behaviour that
matters: broad recall, one document per real page, off-topic pages rejected, and
total graceful degradation when the search instance isn't there.
"""

from datetime import datetime

import pytest

from engine.config import settings
from engine.db.models import Document, Politician
from engine.ingestion import orchestrator
from engine.ingestion.discovery_connector import DiscoveryConnector
from engine.ingestion.queries import discovery_variants


def _result(url, title="Title", content="snippet", published=None, engine="google"):
    return {"url": url, "title": title, "content": content, "publishedDate": published, "engine": engine}


def test_discovery_variants_probe_investigative_angles():
    """A plain name query returns today's news; due-diligence needs the angles
    that surface court records, contracts and old history."""
    subject = Politician(name="Jane Wanjiku", aliases=["JW"], titles=[], swahili_terms=[])
    variants = discovery_variants(subject)

    joined = " ".join(variants).lower()
    assert '"jane wanjiku" court' in joined
    assert "tribunal" in joined and "contract" in joined and "corruption" in joined
    # Reaches deliberately for archived material, not just recent coverage.
    assert "1990s" in joined or "history" in joined
    assert len(variants) <= 40


def test_discover_dedupes_the_same_page_across_engines():
    """The same article found by several engines/URL spellings is one document."""
    results = {
        "q1": [_result("https://www.nation.example/story/abc/")],
        "q2": [_result("http://nation.example/story/abc")],  # same page, different URL form
        "q3": [_result("https://standard.example/other")],
    }
    conn = DiscoveryConnector(base_url="http://searx", searcher=lambda q: results.get(q, []))

    found = conn.discover(["q1", "q2", "q3"])

    assert [d["domain"] for d in found] == ["nation.example", "standard.example"]


def test_discover_skips_aggregator_and_social_shells():
    conn = DiscoveryConnector(
        base_url="http://searx",
        searcher=lambda q: [
            _result("https://google.com/search?q=x"),
            _result("https://facebook.com/post/1"),
            _result("https://realnews.example/story"),
        ],
    )
    found = conn.discover(["q"])
    assert [d["domain"] for d in found] == ["realnews.example"]


def test_fetch_documents_keeps_on_topic_and_drops_homonyms():
    """The SHA-problem guard: a page that never names the subject is dropped."""
    conn = DiscoveryConnector(
        base_url="http://searx",
        searcher=lambda q: [
            _result("https://news.example/a", title="Land tribunal"),
            _result("https://unrelated.example/b", title="Cooking recipes"),
        ],
        fetcher=lambda url: (
            "In 1992 Jane Wanjiku appeared before the land tribunal over a plot transfer."
            if "news.example" in url
            else "A guide to baking sourdough bread at home."
        ),
    )

    docs = conn.fetch_documents("Jane Wanjiku", [], ["q"])

    assert len(docs) == 1
    assert docs[0]["domain"] == "news.example"
    assert "tribunal" in docs[0]["body"]
    assert docs[0]["full_text"] is True


def test_fetch_documents_matches_on_surname_alone():
    """Coverage commonly uses only the surname."""
    conn = DiscoveryConnector(
        base_url="http://searx",
        searcher=lambda q: [_result("https://news.example/a")],
        fetcher=lambda url: "Wanjiku denied the allegations in a statement.",
    )
    assert len(conn.fetch_documents("Jane Wanjiku", [], ["q"])) == 1


def test_discovery_degrades_gracefully_without_an_instance():
    """No SearXNG configured (or unreachable) means no discovery — never an error."""
    conn = DiscoveryConnector(base_url="")
    assert conn.discover(["anything"]) == []
    assert conn.fetch_documents("Someone", [], ["q"]) == []


def test_search_failure_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("searxng down")

    import engine.ingestion.discovery_connector as dc

    monkeypatch.setattr(dc.http, "get", boom)
    conn = DiscoveryConnector(base_url="http://searx")
    assert conn.discover(["q"]) == []


def test_store_documents_is_idempotent_across_runs(db_session):
    """Re-running a subject must not duplicate the same page."""
    subject = Politician(name="Store Probe")
    db_session.add(subject)
    db_session.flush()
    run = orchestrator.plan_run(db_session, subject, datetime(2026, 1, 1), datetime(2026, 7, 1))

    docs = [
        {
            "url": "https://news.example/story",
            "domain": "news.example",
            "title": "A story",
            "body": "Store Probe was named in the filing.",
            "source": "searxng",
            "source_tier": "free",
            "doc_type": "article",
            "published_at": datetime(2026, 3, 1),
        }
    ]

    first = orchestrator._store_documents(db_session, run, subject, docs)
    second = orchestrator._store_documents(db_session, run, subject, docs)

    assert first == 1
    assert second == 0, "re-ingesting the same page must be a no-op"
    assert db_session.query(Document).filter_by(politician_id=subject.id).count() == 1


def test_stored_document_is_immediately_searchable(db_session):
    """Storage feeds the full-text index, so evidence is retrievable at once."""
    subject = Politician(name="Search Probe")
    db_session.add(subject)
    db_session.flush()
    run = orchestrator.plan_run(db_session, subject, datetime(2026, 1, 1), datetime(2026, 7, 1))

    orchestrator._store_documents(
        db_session, run, subject,
        [{
            "url": "https://archive.example/1994",
            "domain": "archive.example",
            "title": "Archive",
            "body": "Search Probe signed the Mombasa terminal concession in 1994.",
            "source": "searxng",
        }],
    )

    from sqlalchemy import text as sql_text

    hits = db_session.execute(
        sql_text(
            "SELECT count(*) FROM documents WHERE politician_id = :pid "
            "AND search_vector @@ websearch_to_tsquery('english', :q)"
        ),
        {"pid": subject.id, "q": "Mombasa terminal concession"},
    ).scalar()
    assert hits == 1


def test_discovery_task_planned_only_when_configured(db_session, monkeypatch):
    from engine.db.models import IngestionTask

    subject = Politician(name="Plan Probe")
    db_session.add(subject)
    db_session.flush()

    monkeypatch.setattr(settings, "searxng_url", "", raising=False)
    run = orchestrator.plan_run(db_session, subject, datetime(2026, 1, 1), datetime(2026, 7, 1))
    connectors = {c for (c,) in db_session.query(IngestionTask.connector).filter_by(run_id=run.id)}
    assert "discovery" not in connectors

    monkeypatch.setattr(settings, "searxng_url", "http://searxng:8080", raising=False)
    monkeypatch.setattr(settings, "enable_discovery", True, raising=False)
    run2 = orchestrator.plan_run(db_session, subject, datetime(2026, 1, 1), datetime(2026, 7, 1))
    connectors2 = {c for (c,) in db_session.query(IngestionTask.connector).filter_by(run_id=run2.id)}
    assert "discovery" in connectors2
