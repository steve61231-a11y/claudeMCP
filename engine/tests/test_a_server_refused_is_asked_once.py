"""Failed: kenyalaw, reddit — HTTPError: 403 Client Error: Forbidden for url: …

A live report on Rigathi Gachagua listed two sources under "Failed", with the
full request URL of every refused query, on the page a CEO reads. Neither was
broken in a way another request could change. Kenya Law's bot wall refuses
the fingerprint of python-requests; Reddit refuses every keyless request from
a cloud address with "403 Blocked". Each run rediscovered both, a dozen times,
and paid for it in time.

What changed, and what each test below holds:

  * a refusal that is the HOST's verdict on this server is remembered for a
    few hours, and nothing — no tier, no connector — asks again meanwhile;
  * Kenya Law is asked through the tiered fetcher, whose second tier presents
    a real browser's TLS handshake, which is what the wall checks;
  * Reddit signs in app-only when REDDIT_CLIENT_ID/SECRET exist, and asks
    oauth.reddit.com, which serves cloud servers;
  * the learning loop no longer reads a refused query as one that was asked
    and found nothing — two runs behind a block used to retire every one;
  * an exhausted SocialCrawl account skips transcripts as configuration,
    instead of failing them three times and calling the stage broken;
  * collection has a ceiling, and article downloads a budget per RUN, as the
    setting always said, instead of per query.
"""

import time
from datetime import datetime, timedelta

import pytest

from engine import stages
from engine.config import settings
from engine.db.models import IngestionTask
from engine.ingestion import fetch_backend, http, learning
from engine.ingestion.fetch_backend import FetchResult
from engine.ingestion.kenya_law_connector import KenyaLawConnector
from engine.ingestion import reddit_connector
from engine.ingestion.reddit_connector import RedditConnector
from engine.tests.test_the_loop_learns import WINDOW, _past_run, _subject

START, END = datetime(2026, 9, 15), datetime(2026, 10, 15)


@pytest.fixture(autouse=True)
def _clean_refusals():
    http.reset_pacing()
    reddit_connector._TOKEN.clear()
    yield
    http.reset_pacing()
    reddit_connector._TOKEN.clear()


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} Client Error: Blocked")


# --- the memory ----------------------------------------------------------------

def test_a_refused_host_is_not_asked_again(monkeypatch):
    calls = []
    monkeypatch.setattr(http, "_pace", lambda host: None)

    class _Session:
        def get(self, url, **kw):
            calls.append(url)
            return _Resp(200)

    monkeypatch.setattr(http, "_shared_session", _Session())
    http.remember_refusal("https://www.reddit.com/search.json", "blocked")
    with pytest.raises(http.HostRefused):
        http.get("https://www.reddit.com/r/Kenya/search.json")
    assert not calls, "a refused host was asked anyway"
    # Other hosts are untouched.
    http.get("https://news.google.com/rss")
    assert calls == ["https://news.google.com/rss"]


def test_a_refusal_expires():
    """A block can be lifted, and a deploy can move to another address."""
    http.remember_refusal("https://new.kenyalaw.org/search/", "403", hours=-0.001)
    assert http.refused("https://new.kenyalaw.org/search/") is None


def test_no_fetch_tier_asks_a_refused_host(monkeypatch):
    tiers = []
    monkeypatch.setattr(fetch_backend, "_via_requests",
                        lambda url, t: tiers.append("requests"))
    monkeypatch.setattr(fetch_backend, "_via_scrapling",
                        lambda url, t: tiers.append("impersonating"))
    http.remember_refusal("https://new.kenyalaw.org/x", "403")
    result = fetch_backend.fetch_html("https://new.kenyalaw.org/search/?q=a")
    assert not result.ok and not tiers


# --- Kenya Law ---------------------------------------------------------------

def test_kenya_law_is_asked_with_a_browser_fingerprint(monkeypatch):
    """The first tier is refused; the second is what reaches the page."""
    page = ('<html><body>' + "<p>filler</p>" * 900 +
            '<a href="/akn/ke/judgment/kehc/2026/1">Republic v Ruto, judgment '
            'delivered 3 October 2026</a></body></html>')
    monkeypatch.setattr(fetch_backend, "_via_requests",
                        lambda url, t: FetchResult(url, "", 403, "requests", True))
    monkeypatch.setattr(fetch_backend, "_via_scrapling",
                        lambda url, t: FetchResult(url, page, 200, "scrapling", False))
    monkeypatch.setattr(settings, "enable_scrapling", True)
    found = KenyaLawConnector().fetch("William Ruto", [], START, END)
    assert found, "the impersonating tier was not used"


def test_kenya_law_refusing_this_server_is_remembered_and_said(monkeypatch):
    asked = []

    def refused(url, t):
        asked.append(url)
        return FetchResult(url, "", 403, "requests", True)

    monkeypatch.setattr(fetch_backend, "_via_requests", refused)
    monkeypatch.setattr(fetch_backend, "_via_scrapling", refused)
    first = KenyaLawConnector()
    assert first.fetch("William Ruto", [], START, END) == []
    assert "refused this server" in first.last_error

    second = KenyaLawConnector()
    assert second.fetch("Rigathi Gachagua", [], START, END) == []
    assert len(asked) == 2, "the second query asked a host that had refused us"
    # And it is never reported as "no court record".
    assert "refused" in second.last_error


# --- Reddit --------------------------------------------------------------------

def test_reddit_keyless_is_refused_once_then_not_asked(monkeypatch):
    monkeypatch.setattr(settings, "reddit_client_id", None)
    monkeypatch.setattr(settings, "reddit_client_secret", None)
    asked = []

    def get(url, **kw):
        reason = http.refused(url)
        if reason:
            raise http.HostRefused(reason)
        asked.append(url)
        return _Resp(403)

    monkeypatch.setattr(http, "get", get)
    first = RedditConnector()
    assert first.fetch("Rigathi Gachagua", [], START, END) == []
    second = RedditConnector()
    assert second.fetch("Rigathi Gachagua", [], START, END, query="Gachagua Nyeri") == []
    assert len(asked) == 1, f"Reddit was asked {len(asked)} times after refusing"
    assert "REDDIT_CLIENT_ID" in second.last_error, "the operator is not told the fix"


def test_reddit_signs_in_when_it_can(monkeypatch):
    monkeypatch.setattr(settings, "reddit_client_id", "id")
    monkeypatch.setattr(settings, "reddit_client_secret", "secret")
    posted = []

    def post(url, **kw):
        posted.append((url, kw.get("auth")))
        return _Resp(200, {"access_token": "tok", "expires_in": 3600})

    monkeypatch.setattr(reddit_connector.requests, "post", post)
    asked = []

    def get(url, params=None, headers=None, **kw):
        asked.append((url, (headers or {}).get("Authorization")))
        return _Resp(200, {"data": {"children": [{"data": {
            "id": "abc", "title": "Gachagua in Nyeri", "created_utc": time.time(),
            "author": "someone", "permalink": "/r/Kenya/comments/abc"}}]}})

    monkeypatch.setattr(http, "get", get)
    found = RedditConnector().fetch("Rigathi Gachagua", [], datetime.utcnow() - timedelta(days=30),
                                    datetime.utcnow() + timedelta(days=1))
    assert posted and posted[0][1] == ("id", "secret")
    assert all(url.startswith("https://oauth.reddit.com/") for url, _ in asked)
    assert all(auth == "bearer tok" for _, auth in asked)
    assert found


# --- the learning loop --------------------------------------------------------

def test_a_refused_query_is_not_a_query_that_found_nothing(db_session):
    politician = _subject(db_session)
    for _ in range(2):
        run = _past_run(db_session, politician, {}, source="reddit")
        db_session.add(IngestionTask(
            run_id=run.id, connector="reddit", platform="reddit", endpoint="search",
            query="Mudavadi Treasury", status="done", result_count=0,
            error="returned nothing: Reddit refuses keyless requests from this server"))
        db_session.commit()
    memory = learning.recall(db_session, politician, "reddit")
    assert "Mudavadi Treasury" not in memory, (
        "two runs behind a block retired a question nobody had answered")


# --- transcripts on an empty account -------------------------------------------

def test_an_empty_account_skips_transcripts_instead_of_failing_them(monkeypatch):
    from types import SimpleNamespace

    from engine import pipeline

    monkeypatch.setattr(settings, "socialcrawl_api_key", "set")
    stages.reset()
    run = SimpleNamespace(stats={"credit_balance_after": 0.0})
    # No database is touched: the balance answers the question first.
    pipeline._fetch_transcripts_for_top_videos(None, None, None, START, END, run)
    ledger = stages.current()
    assert not ledger.failures, "an empty account was reported as a broken stage"
    assert "transcripts" in ledger.summary()["skipped"]


# --- the budget is per run, and collection has a ceiling ----------------------

def test_article_downloads_are_budgeted_per_run(monkeypatch):
    from engine.ingestion import article_text

    monkeypatch.setattr(settings, "enable_article_text", True)
    monkeypatch.setattr(settings, "article_text_max_fetch", 5)
    fetched = []
    monkeypatch.setattr(article_text, "extract_body",
                        lambda url, n: (fetched.append(url) or "body", "requests"))
    article_text._BUDGET_LEFT.clear()

    def batch(prefix):
        return [{"source_type": "article", "text": "t",
                 "raw_payload": {"url": f"https://example.co.ke/{prefix}{i}"}}
                for i in range(4)]

    article_text.enrich_with_article_text(batch("a"), run_id="run-1")
    article_text.enrich_with_article_text(batch("b"), run_id="run-1")
    assert len(fetched) == 5, f"{len(fetched)} downloads against a per-run budget of 5"
    article_text.enrich_with_article_text(batch("c"), run_id="run-2")
    assert len(fetched) == 9, "one run's budget was charged to another"


def test_a_stop_signal_ends_the_download_queue(monkeypatch):
    from engine.ingestion import article_text

    monkeypatch.setattr(settings, "enable_article_text", True)
    fetched = []
    monkeypatch.setattr(article_text, "extract_body",
                        lambda url, n: (fetched.append(url) or "body", "requests"))
    items = [{"source_type": "article", "text": "t",
              "raw_payload": {"url": f"https://example.co.ke/{i}"}} for i in range(5)]
    article_text.enrich_with_article_text(items, should_stop=lambda: True)
    assert not fetched


def test_collection_stops_at_its_ceiling(db_session, monkeypatch):
    from engine.db.models import IngestionRun, Politician
    from engine.ingestion import orchestrator

    politician = Politician(name="Edwin Sifuna", aliases=[], keywords=[], titles=[],
                            swahili_terms=[], tracked_hashtags=[], social_handles={})
    db_session.add(politician)
    db_session.commit()
    run = IngestionRun(politician_id=politician.id, window_start=START, window_end=END,
                       status="pending")
    db_session.add(run)
    db_session.flush()
    for i in range(6):
        db_session.add(IngestionTask(run_id=run.id, connector="mock", platform="mixed",
                                     endpoint="fixtures", query=f"q{i}", status="pending"))
    db_session.commit()

    def slow(session_factory, task_id):
        time.sleep(3)

    monkeypatch.setattr(orchestrator, "_execute_task_safe", slow)
    monkeypatch.setattr(orchestrator, "MIN_COLLECTION_SECONDS", 1)
    monkeypatch.setattr(settings, "ingestion_deadline_seconds", 1)
    monkeypatch.setattr(settings, "ingestion_workers", 2)
    started = time.monotonic()
    result = orchestrator.execute_run(db_session, run.id, max_workers=2)
    elapsed = time.monotonic() - started

    assert elapsed < 30, f"collection took {elapsed:.0f}s against a ceiling"
    cut = result.stats["collection_deadline"]
    assert cut["not_started"] == 4 and cut["still_running"] == 2
    statuses = sorted(t.status for t in db_session.query(IngestionTask)
                      .filter_by(run_id=run.id))
    assert statuses.count("skipped_deadline") == 4
    assert orchestrator.collection_stopped(run.id)
