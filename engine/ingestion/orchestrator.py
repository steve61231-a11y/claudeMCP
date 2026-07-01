"""Fan-out ingestion orchestrator.

Replaces the pick-one connector factory for run execution: a run is planned
into per-(connector, platform, endpoint, query) IngestionTask rows, executed
in parallel with pagination, per-page durable upserts (crash-safe/resumable
via page_cursor), and a client-side credit budget guardrail.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from engine.config import settings
from engine.db.models import AuthorProfile, IngestionRun, IngestionTask, Politician, RawMention
from engine.ingestion import queries
from engine.ingestion.base import IngestedMention
from engine.processing import cleaning

CURATED_DIR = Path(__file__).resolve().parent.parent.parent / "mock-data" / "curated"

MAX_PAGES_PER_TASK = 5
COMMENT_TOP_N_POSTS = 10
DEFAULT_MAX_WORKERS = 8


def plan_run(
    db: Session,
    politician: Politician,
    window_start: datetime,
    window_end: datetime,
    credit_budget: float = 0.0,
) -> IngestionRun:
    """Expands query variants × endpoints into task rows. All configured
    connectors contribute tasks — sources are combined, never either/or."""
    run = IngestionRun(
        politician_id=politician.id,
        window_start=window_start,
        window_end=window_end,
        status="pending",
        credit_budget=credit_budget,
    )
    db.add(run)
    db.flush()

    tasks: list[IngestionTask] = []

    if settings.socialcrawl_api_key:
        from engine.ingestion import socialcrawl_connector as sc

        text_qs = queries.text_variants(politician)
        hashtag_qs = queries.hashtag_variants(politician)
        for platform, path, source_type, query_kind in sc.SEARCH_ENDPOINTS:
            for query in text_qs if query_kind == "text" else hashtag_qs:
                tasks.append(
                    IngestionTask(
                        run_id=run.id,
                        connector="socialcrawl",
                        platform=platform,
                        endpoint=path,
                        query=query,
                        params={"source_type": source_type},
                    )
                )
        tasks.append(
            IngestionTask(
                run_id=run.id,
                connector="socialcrawl",
                platform="web",
                endpoint=sc.SOCIALCRAWL_BRAND_MENTIONS_PATH,
                query=politician.name,
            )
        )
        for platform, handle in (politician.social_handles or {}).items():
            if handle:
                tasks.append(
                    IngestionTask(
                        run_id=run.id,
                        connector="socialcrawl",
                        platform=platform,
                        endpoint="profile_activity",
                        query=str(handle),
                    )
                )

    if settings.newsapi_key:
        news_query = " OR ".join(f'"{q}"' for q in queries.text_variants(politician)[:6])
        tasks.append(
            IngestionTask(run_id=run.id, connector="newsapi", platform="news", endpoint="everything", query=news_query)
        )

    slug = politician.name.lower().replace(" ", "_")
    if (CURATED_DIR / f"{slug}.json").exists():
        tasks.append(
            IngestionTask(run_id=run.id, connector="curated", platform="mixed", endpoint="curated", query=politician.name)
        )

    if not tasks:
        # Nothing configured — keep local dev/tests working on mock fixtures.
        tasks.append(
            IngestionTask(run_id=run.id, connector="mock", platform="mixed", endpoint="fixtures", query=politician.name)
        )

    db.add_all(tasks)
    db.commit()
    return run


def execute_run(db: Session, run_id: str, max_workers: int = DEFAULT_MAX_WORKERS) -> IngestionRun:
    """Executes all pending tasks for a run in parallel. Each worker uses its
    own session; pages are committed as they land, so a crash loses at most
    one in-flight page and a re-run resumes from the stored cursors."""
    session_factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)

    run = db.get(IngestionRun, run_id)
    run.status = "running"
    run.started_at = datetime.utcnow()
    db.commit()

    task_ids = [
        t_id
        for (t_id,) in db.query(IngestionTask.id)
        .filter(IngestionTask.run_id == run_id, IngestionTask.status.in_(["pending", "running"]))
        .all()
    ]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(lambda t_id: _execute_task_safe(session_factory, t_id), task_ids))

    db.expire_all()
    run = db.get(IngestionRun, run_id)
    statuses = [t.status for t in db.query(IngestionTask).filter_by(run_id=run_id).all()]
    run.status = "completed" if all(s in ("done", "skipped_budget") for s in statuses) else "failed"
    run.finished_at = datetime.utcnow()
    run.stats = _run_stats(db, run)
    db.commit()
    return run


def _run_stats(db: Session, run: IngestionRun) -> dict:
    by_platform: dict[str, int] = {}
    for platform, count in (
        db.query(RawMention.platform, func.count())
        .filter(RawMention.run_id == run.id)
        .group_by(RawMention.platform)
        .all()
    ):
        by_platform[platform] = int(count)
    return {
        "mentions_by_platform": by_platform,
        "mentions_total": sum(by_platform.values()),
        "credits_spent": run.credits_spent,
    }


def _execute_task_safe(session_factory, task_id: str) -> None:
    session = session_factory()
    try:
        _execute_task(session, task_id)
    except Exception as exc:  # noqa: BLE001 — a task failure must not kill the run
        session.rollback()
        task = session.get(IngestionTask, task_id)
        task.status = "failed"
        task.error = f"{type(exc).__name__}: {exc}"[:2000]
        session.commit()
    finally:
        session.close()


def _claim_task(session: Session, task_id: str) -> IngestionTask | None:
    """FOR UPDATE SKIP LOCKED claim so concurrent executors (multi-process
    runs) never double-fetch the same task."""
    task = (
        session.query(IngestionTask)
        .filter(IngestionTask.id == task_id, IngestionTask.status.in_(["pending", "running"]))
        .with_for_update(skip_locked=True)
        .first()
    )
    if task:
        task.status = "running"
        session.commit()
    return task


def _execute_task(session: Session, task_id: str) -> None:
    task = _claim_task(session, task_id)
    if not task:
        return
    run = session.get(IngestionRun, task.run_id)
    politician = session.get(Politician, run.politician_id)

    if task.connector == "socialcrawl":
        _execute_socialcrawl_task(session, run, task, politician)
    elif task.connector == "newsapi":
        _execute_newsapi_task(session, run, task, politician)
    else:
        _execute_bulk_connector_task(session, run, task, politician)

    if task.status == "running":
        task.status = "done"
    session.commit()


def _spend_credits(session: Session, run_id: str, amount: float) -> float:
    """Atomic credit accounting across parallel workers; returns new total."""
    result = session.execute(
        sql_text("UPDATE ingestion_runs SET credits_spent = credits_spent + :amt WHERE id = :id RETURNING credits_spent"),
        {"amt": amount, "id": run_id},
    )
    session.commit()
    return float(result.scalar())


def _budget_exhausted(run: IngestionRun, spent: float) -> bool:
    return bool(run.credit_budget) and spent >= run.credit_budget


def _execute_socialcrawl_task(session: Session, run: IngestionRun, task: IngestionTask, politician: Politician) -> None:
    from engine.ingestion import socialcrawl_connector as sc

    connector = sc.SocialCrawlConnector()

    if task.endpoint == "profile_activity":
        mentions = connector.fetch_profile_activity(
            {task.platform: task.query}, run.window_start, run.window_end, source_type="own_activity"
        )
        spent = _spend_credits(session, run.id, sc.DEFAULT_CREDIT_COST)
        task.credits_spent += sc.DEFAULT_CREDIT_COST
        task.result_count += _store_mentions(session, run, task, politician, mentions)
        return

    if task.endpoint == sc.SOCIALCRAWL_BRAND_MENTIONS_PATH:
        current = session.query(IngestionRun.credits_spent).filter_by(id=run.id).scalar() or 0.0
        if _budget_exhausted(run, current + sc.credit_cost(task.endpoint)):
            task.status = "skipped_budget"
            return
        mentions = connector._fetch_brand_mentions(task.query, run.window_start, run.window_end)
        _spend_credits(session, run.id, sc.credit_cost(task.endpoint))
        task.credits_spent += sc.credit_cost(task.endpoint)
        task.result_count += _store_mentions(session, run, task, politician, mentions)
        return

    source_type = (task.params or {}).get("source_type", "post")
    discovered_posts: list[IngestedMention] = []
    while task.page_cursor <= MAX_PAGES_PER_TASK:
        current = session.query(IngestionRun.credits_spent).filter_by(id=run.id).scalar() or 0.0
        if _budget_exhausted(run, current + sc.credit_cost(task.endpoint)):
            task.status = "skipped_budget"
            break
        idem = f"{task.id}:{task.page_cursor}"
        mentions, has_more = connector.search_page(
            task.platform,
            task.endpoint,
            task.query,
            task.page_cursor,
            run.window_start,
            run.window_end,
            default_source_type=source_type,
            idempotency_key=idem,
        )
        _spend_credits(session, run.id, sc.credit_cost(task.endpoint))
        task.credits_spent += sc.credit_cost(task.endpoint)
        in_window = [m for m in mentions if run.window_start <= m["posted_at"] <= run.window_end]
        discovered_posts.extend(in_window)
        task.result_count += _store_mentions(session, run, task, politician, in_window)
        task.page_cursor += 1
        session.commit()  # durable per page: cursor + counts + mentions
        if not has_more:
            break

    # Grassroots reactions: comments for the most-engaged discovered posts.
    if task.platform in sc.SocialCrawlConnector._COMMENT_ENDPOINTS and task.status == "running":
        top_posts = sorted(discovered_posts, key=_engagement_score, reverse=True)[:COMMENT_TOP_N_POSTS]
        for post in top_posts:
            current = session.query(IngestionRun.credits_spent).filter_by(id=run.id).scalar() or 0.0
            if _budget_exhausted(run, current + sc.DEFAULT_CREDIT_COST):
                break
            payload = post.get("raw_payload") or {}
            post_id = payload.get("id") or payload.get("video_id") or payload.get("post_id")
            if not post_id:
                continue
            try:
                comments, _ = connector.comments_page(task.platform, str(post_id), idempotency_key=f"{task.id}:c:{post_id}")
            except Exception:
                continue
            _spend_credits(session, run.id, sc.DEFAULT_CREDIT_COST)
            task.credits_spent += sc.DEFAULT_CREDIT_COST
            task.result_count += _store_mentions(session, run, task, politician, comments)
            session.commit()


def _execute_newsapi_task(session: Session, run: IngestionRun, task: IngestionTask, politician: Politician) -> None:
    from engine.ingestion.news_connector import NewsApiConnector

    connector = NewsApiConnector()
    while task.page_cursor <= MAX_PAGES_PER_TASK:
        mentions, has_more = connector.search_page(task.query, task.page_cursor, run.window_start, run.window_end)
        task.result_count += _store_mentions(session, run, task, politician, mentions)
        task.page_cursor += 1
        session.commit()
        if not has_more:
            break


def _execute_bulk_connector_task(session: Session, run: IngestionRun, task: IngestionTask, politician: Politician) -> None:
    """Curated fixtures and mock data have no pagination — one bulk fetch."""
    if task.connector == "curated":
        from engine.ingestion.curated_source import CuratedResearchConnector

        connector = CuratedResearchConnector()
    else:
        from engine.ingestion.mock_source import MockIngestionConnector

        connector = MockIngestionConnector()
    mentions = connector.fetch(politician.name, politician.aliases or [], run.window_start, run.window_end)
    task.result_count += _store_mentions(session, run, task, politician, mentions)


def _engagement_score(mention: IngestedMention) -> int:
    engagement = mention.get("engagement") or {}
    return sum(int(engagement.get(k, 0) or 0) for k in ("likes", "shares", "comments", "views"))


def _source_url(raw: dict) -> str | None:
    for key in ("url", "link", "share_url", "video_url", "post_url", "permalink"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _store_mentions(
    session: Session,
    run: IngestionRun,
    task: IngestionTask,
    politician: Politician,
    mentions: list[IngestedMention],
) -> int:
    """Cleans and upserts one page of mentions with full provenance.
    ON CONFLICT DO NOTHING against the (politician, platform, content_hash)
    unique key makes re-runs and cross-task overlaps idempotent."""
    if not mentions:
        return 0
    cleaned = [m for m in cleaning.clean_mentions(mentions) if not m["is_spam"]]
    if not cleaned:
        return 0

    now = datetime.utcnow()
    rows = []
    for item in cleaned:
        raw = item.get("raw_payload") or {}
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "politician_id": politician.id,
                "platform": item["platform"],
                "source_type": item["source_type"],
                "author_handle": item["author_handle"],
                "text": item["text"],
                "posted_at": item["posted_at"],
                "engagement_json": item.get("engagement") or {},
                "raw_payload": raw,
                "content_hash": item["content_hash"],
                "is_spam": 0,
                "run_id": run.id,
                "source_endpoint": task.endpoint,
                "source_url": _source_url(raw),
                "fetched_at": now,
                "language": queries.detect_language(item["text"]),
                "link_checked": 0,
                "created_at": now,
            }
        )

    stmt = pg_insert(RawMention).values(rows).on_conflict_do_nothing(
        constraint="uq_raw_mentions_politician_platform_hash"
    )
    result = session.execute(stmt)
    _upsert_author_profiles(session, task.platform, cleaned)
    session.commit()
    return result.rowcount or 0


def _upsert_author_profiles(session: Session, platform: str, cleaned: list[dict]) -> None:
    from engine.ingestion.socialcrawl_connector import SocialCrawlConnector

    profiles: dict[tuple[str, str], dict] = {}
    for item in cleaned:
        profile = SocialCrawlConnector.extract_author_profile(
            item.get("platform") or platform, item.get("raw_payload") or {}
        )
        if not profile:
            profile = {
                "platform": item.get("platform") or platform,
                "handle": item["author_handle"],
                "display_name": None,
                "follower_count": None,
                "profile_url": None,
            }
        if profile["handle"] == "unknown":
            continue
        key = (profile["platform"], profile["handle"])
        # Keep the richest version seen in this batch (follower count wins).
        if key not in profiles or (profile.get("follower_count") and not profiles[key].get("follower_count")):
            profiles[key] = profile

    if not profiles:
        return
    now = datetime.utcnow()
    rows = [
        {"id": str(uuid.uuid4()), **p, "last_refreshed": now, "created_at": now} for p in profiles.values()
    ]
    stmt = pg_insert(AuthorProfile).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_author_profiles_platform_handle",
        set_={
            "follower_count": stmt.excluded.follower_count,
            "display_name": stmt.excluded.display_name,
            "profile_url": stmt.excluded.profile_url,
            "last_refreshed": stmt.excluded.last_refreshed,
        },
        where=stmt.excluded.follower_count.isnot(None),
    )
    session.execute(stmt)
