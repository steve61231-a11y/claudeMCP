from datetime import datetime

import pytest

from engine.config import settings
from engine.db.models import AuthorProfile, IngestionTask, Politician, RawMention
from engine.ingestion import orchestrator
from engine.ingestion.base import IngestedMention
from engine.ingestion.socialcrawl_connector import SocialCrawlConnector

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59))


@pytest.fixture(autouse=True)
def _no_keyless_network(monkeypatch):
    # These tests assert SocialCrawl/mock connector planning. GDELT/Wayback are
    # default-on in production but would add tasks and make real network calls,
    # so keep this module hermetic by turning them off.
    monkeypatch.setattr(settings, "enable_gdelt", False, raising=False)
    monkeypatch.setattr(settings, "enable_wayback", False, raising=False)


def make_politician(db):
    politician = Politician(
        name="John Mbadi",
        aliases=["Mbadi"],
        keywords=["Treasury"],
        titles=["CS"],
        swahili_terms=[],
        tracked_hashtags=["Mbadi"],
        social_handles={"tiktok": "johnmbadi"},
    )
    db.add(politician)
    db.commit()
    return politician


def fake_mention(text, platform="tiktok", author="user1", followers=None, post_id="p1"):
    raw = {"id": post_id, "url": f"https://example.com/{post_id}"}
    if followers is not None:
        raw["author"] = {"username": author, "follower_count": followers}
    return IngestedMention(
        platform=platform,
        source_type="video",
        author_handle=author,
        text=text,
        posted_at=datetime(2026, 6, 15),
        engagement={"likes": 5},
        raw_payload=raw,
    )


def test_plan_run_without_keys_creates_mock_task(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)

    run = orchestrator.plan_run(db_session, politician, *WINDOW)

    tasks = db_session.query(IngestionTask).filter_by(run_id=run.id).all()
    assert [t.connector for t in tasks] == ["mock"]


def test_plan_run_with_socialcrawl_fans_out_queries_and_endpoints(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)

    run = orchestrator.plan_run(db_session, politician, *WINDOW, credit_budget=500)

    tasks = db_session.query(IngestionTask).filter_by(run_id=run.id).all()
    endpoints = {t.endpoint for t in tasks}
    # search fan-out across platforms, plus composite + profile tracking
    assert "/v1/tiktok/search" in endpoints
    assert "/v1/linkedin/search/posts" in endpoints
    assert "/v1/google_news/search" in endpoints
    assert "/v1/prism/brand-mentions" in endpoints
    assert "profile_activity" in endpoints
    # multiple query variants per search endpoint
    tiktok_search = [t for t in tasks if t.endpoint == "/v1/tiktok/search"]
    assert len(tiktok_search) >= 2
    assert run.credit_budget == 500


def test_execute_run_paginates_and_stores_with_provenance(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)

    pages_requested = []

    def fake_search_page(self, platform, path, query, page, ws, we, default_source_type="post", idempotency_key=None):
        pages_requested.append((path, query, page))
        if page >= 2:
            return [fake_mention(f"Mbadi update {platform} {query} p{page}", platform=platform, post_id=f"{query}-{page}")], False
        return [fake_mention(f"Mbadi update {platform} {query} p{page}", platform=platform, post_id=f"{query}-{page}")], True

    monkeypatch.setattr(SocialCrawlConnector, "search_page", fake_search_page)
    monkeypatch.setattr(SocialCrawlConnector, "comments_page", lambda self, *a, **k: ([], False))
    monkeypatch.setattr(SocialCrawlConnector, "_fetch_brand_mentions", lambda self, *a: [])
    monkeypatch.setattr(SocialCrawlConnector, "fetch_profile_activity", lambda self, *a, **k: [])

    run = orchestrator.execute_run(db_session, run.id, max_workers=2)

    assert run.status == "completed"
    assert run.credits_spent > 0
    # every search task paginated to page 2
    search_pages = {p for (_, _, p) in pages_requested}
    assert search_pages == {1, 2}
    mention = db_session.query(RawMention).filter(RawMention.source_endpoint == "/v1/tiktok/search").first()
    assert mention is not None
    assert mention.run_id == run.id
    assert mention.source_url.startswith("https://example.com/")
    assert mention.fetched_at is not None
    assert mention.language == "en"
    assert run.stats["mentions_total"] == db_session.query(RawMention).filter_by(run_id=run.id).count()


def test_execute_run_respects_credit_budget(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW, credit_budget=3)

    monkeypatch.setattr(
        SocialCrawlConnector,
        "search_page",
        lambda self, platform, path, query, page, ws, we, default_source_type="post", idempotency_key=None: (
            [fake_mention(f"Mbadi {platform} {query} {page}", platform=platform, post_id=f"{query}{page}")],
            True,
        ),
    )
    monkeypatch.setattr(SocialCrawlConnector, "comments_page", lambda self, *a, **k: ([], False))
    monkeypatch.setattr(SocialCrawlConnector, "_fetch_brand_mentions", lambda self, *a: [])
    monkeypatch.setattr(SocialCrawlConnector, "fetch_profile_activity", lambda self, *a, **k: [])

    run = orchestrator.execute_run(db_session, run.id, max_workers=1)

    statuses = {t.status for t in db_session.query(IngestionTask).filter_by(run_id=run.id).all()}
    assert "skipped_budget" in statuses
    assert run.credits_spent <= 3 + 20  # at most one composite overshoot


def test_execute_task_resumes_from_stored_cursor(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)

    task = (
        db_session.query(IngestionTask)
        .filter_by(run_id=run.id, endpoint="/v1/tiktok/search")
        .first()
    )
    task.page_cursor = 4  # simulate a crash after page 3
    other_tasks = db_session.query(IngestionTask).filter(
        IngestionTask.run_id == run.id, IngestionTask.id != task.id
    )
    for t in other_tasks:
        t.status = "done"
    db_session.commit()

    pages_requested = []

    def fake_search_page(self, platform, path, query, page, ws, we, default_source_type="post", idempotency_key=None):
        pages_requested.append(page)
        return [fake_mention(f"Mbadi {query} {page}", post_id=f"{query}{page}")], True

    monkeypatch.setattr(SocialCrawlConnector, "search_page", fake_search_page)
    monkeypatch.setattr(SocialCrawlConnector, "comments_page", lambda self, *a, **k: ([], False))

    orchestrator.execute_run(db_session, run.id, max_workers=1)

    assert min(pages_requested) == 4  # resumed, not restarted
    assert max(pages_requested) == orchestrator.MAX_PAGES_PER_TASK


def test_store_mentions_upserts_author_profiles_with_followers(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)
    task = db_session.query(IngestionTask).filter_by(run_id=run.id).first()

    mentions = [
        fake_mention("Mbadi speech clip", author="bigcreator", followers=50000, post_id="a"),
        fake_mention("Mbadi again", author="smallfry", followers=12, post_id="b"),
    ]
    stored = orchestrator._store_mentions(db_session, run, task, politician, mentions)

    assert stored == 2
    big = db_session.query(AuthorProfile).filter_by(handle="bigcreator").one()
    assert big.follower_count == 50000
    assert big.platform == "tiktok"


def test_store_mentions_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)
    task = db_session.query(IngestionTask).filter_by(run_id=run.id).first()

    mentions = [fake_mention("Mbadi duplicate test", post_id="dup")]
    assert orchestrator._store_mentions(db_session, run, task, politician, mentions) == 1
    assert orchestrator._store_mentions(db_session, run, task, politician, mentions) == 0
    assert db_session.query(RawMention).filter_by(politician_id=politician.id).count() == 1
