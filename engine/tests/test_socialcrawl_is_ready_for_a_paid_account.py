"""SocialCrawl, ready for the day the account is paid for.

The founder, 24 October 2026: "optimise the app for all the SocialCrawl
things we might need, so that when I pay for it, everything just runs." The
account had been unfunded, so nothing exercised the paid path — and reading
each endpoint's contract in SocialCrawl's own catalogue found what a first
paid run would have hit:

- X searched through `twitter/ai-search`, which answers with xAI Grok's
  written summary under keys the connector never read: 5 credits, nothing
  stored, "no chatter on X". A model's answer is not a source anyway.
- Facebook — where most Kenyans are — was reachable only through known page
  handles; keyword search existed and was unused.
- Cursor-paged endpoints were asked for `page=2` and served page 1 again, so
  no run read past the first page of any of them.
- The period went to every endpoint as `date_from`/`date_to`, which most of
  them do not read, so they answered with their all-time best.
- The brand-mentions composite costs 50 credits; the budget table said 20.
- A 402 was rediscovered by every remaining task of the run.
"""

from datetime import datetime

from engine.ingestion import http
from engine.ingestion import socialcrawl_connector as sc
from engine.ingestion.socialcrawl_connector import SocialCrawlConnector

WS, WE = datetime(2026, 10, 1), datetime(2026, 10, 24)


def _params(path, query="Jane Wanjiru", page=1, cursor=None, seen=None):
    return SocialCrawlConnector._search_params(path, query, page, WS, WE, cursor, seen)


def test_no_search_collects_a_models_answer():
    paths = {p for _pl, p, _st, _k in sc.SEARCH_ENDPOINTS}
    assert "/v1/twitter/ai-search" not in paths
    assert "/v1/twitter/search/tweets" in paths
    import inspect

    assert "ai-search" not in inspect.getsource(SocialCrawlConnector._fetch_discovery)


def test_facebook_is_searched_by_keyword():
    assert ("facebook", "/v1/facebook/search/posts", "post", "text") in sc.SEARCH_ENDPOINTS


def test_each_endpoint_is_given_the_period_in_its_own_form():
    tweets = _params("/v1/twitter/search/tweets")
    assert tweets["query"] == '"Jane Wanjiru" since:2026-10-01 until:2026-10-25'
    news = _params("/v1/google_news/search")
    assert (news["from"], news["to"], news["location_code"]) == ("2026-10-01", "2026-10-24", "2404")
    for path in ("/v1/threads/search", "/v1/facebook/search/posts"):
        p = _params(path)
        assert (p["start_date"], p["end_date"]) == ("2026-10-01", "2026-10-24")
    assert _params("/v1/tiktok/search")["date_posted"] == "this-month"
    assert _params("/v1/linkedin/search/posts")["date_posted"] == "past_month"
    assert _params("/v1/tiktok/search/hashtag", "#Wanjiru 2027") == {"hashtag": "Wanjiru2027"}


def test_a_cursor_and_a_seen_id_go_only_where_they_are_understood():
    p = _params("/v1/twitter/search/tweets", cursor="abc", seen="run-1")
    assert p["cursor"] == "abc" and p["seen"] == "run-1"
    q = _params("/v1/youtube/search", cursor="abc", seen="run-1")
    assert "cursor" not in q and "seen" not in q


class _Response:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_a_cursor_endpoint_reports_its_next_page_and_stops_without_one(monkeypatch):
    pages = iter([
        _Response(200, {"data": {"items": [{"post": {"id": "1", "content": {"text": "a"},
                                                     "published_at": "2026-10-10T00:00:00Z"}}]},
                        "pagination": {"next_cursor": "c2"}}),
        _Response(200, {"data": {"items": [{"post": {"id": "2", "content": {"text": "b"},
                                                     "published_at": "2026-10-11T00:00:00Z"}}]},
                        "pagination": {"next_cursor": None}}),
    ])
    asked = []
    monkeypatch.setattr(http, "get", lambda url, params=None, **kw: asked.append(params) or next(pages))
    connector = SocialCrawlConnector(api_key="sc_test")
    _m, more = connector.search_page("twitter", "/v1/twitter/search/tweets", "x", 1, WS, WE)
    assert more is True and connector.next_cursor == "c2"
    _m, more = connector.search_page("twitter", "/v1/twitter/search/tweets", "x", 2, WS, WE,
                                     cursor=connector.next_cursor)
    assert more is False and asked[1]["cursor"] == "c2"


def test_an_empty_account_is_told_once_not_rediscovered(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: _Response(402, text="INSUFFICIENT_CREDITS"))
    connector = SocialCrawlConnector(api_key="sc_test")
    try:
        connector.search_page("tiktok", "/v1/tiktok/search", "x", 1, WS, WE)
    except Exception:  # noqa: BLE001 — the task records the failure
        pass
    assert connector.credits_exhausted
    reason = http.refused(sc.SOCIALCRAWL_BASE_URL + "/v1/threads/search")
    assert reason and "402" in reason and "SOCIALCRAWL_API_KEY" in reason


def test_the_budget_table_charges_what_the_catalogue_does():
    assert sc.credit_cost(sc.SOCIALCRAWL_BRAND_MENTIONS_PATH) == 50.0
    assert sc.credit_cost("/v1/linkedin/search/posts") == 5.0


def test_the_orchestrator_follows_the_cursor_it_was_given(db_session, monkeypatch, no_live_sources):
    from engine.config import settings
    from engine.ingestion import orchestrator
    from engine.tests.test_orchestrator import WINDOW, fake_mention, make_politician

    monkeypatch.setattr(settings, "socialcrawl_api_key", "sc_test")
    cursors = []

    def search_page(self, platform, path, query, page, ws, we, default_source_type="post",
                    idempotency_key=None, cursor=None, seen=None):
        cursors.append((path, cursor, seen))
        self.next_cursor = None if cursor else "next-1"
        return [fake_mention(f"Mbadi {path} {page}", platform=platform, post_id=f"{path}{page}")], cursor is None

    monkeypatch.setattr(SocialCrawlConnector, "search_page", search_page)
    monkeypatch.setattr(SocialCrawlConnector, "comments_page", lambda self, *a, **k: ([], False))
    monkeypatch.setattr(SocialCrawlConnector, "check_balance", lambda self: 1000.0)
    monkeypatch.setattr(SocialCrawlConnector, "_fetch_brand_mentions", lambda self, *a: [])
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW, credit_budget=0)
    orchestrator.execute_run(db_session, run.id, max_workers=1)
    tweets = [c for c in cursors if c[0] == "/v1/twitter/search/tweets"]
    assert tweets[:2] == [("/v1/twitter/search/tweets", None, f"run-{run.id}"[:64]),
                          ("/v1/twitter/search/tweets", "next-1", f"run-{run.id}"[:64])]
