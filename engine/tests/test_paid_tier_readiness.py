"""Turning on the paid social backbone must change every surface, and must
never be the thing that breaks a run.

The report path resolved the tier per run and scheduled SocialCrawl tasks; the
issue map swept four free news and forum sources and nothing else, so paying
for SocialCrawl would have improved the report and left the map exactly as it
was.
"""

import pytest

from engine.reports import issue_map


class _Fake:
    """Stands in for whichever connector the sweep reaches for."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    credits_exhausted = False

    def fetch(self, query, aliases, ws, we):
        _Fake.calls.append(query)
        return [{"text": f"from {query}", "raw_payload": {"url": f"https://x/{len(_Fake.calls)}"}}]

    def search_page(self, platform, path, query, page, ws, we, default_source_type="post", seen=None, **k):
        _Fake.calls.append(query)
        return [{"text": f"from {query} on {path}",
                 "raw_payload": {"url": f"https://x/{len(_Fake.calls)}"}}], False


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    _Fake.calls = []
    # Discovered, not listed — see conftest.source_flags().
    from engine.tests.conftest import source_flags
    for flag in source_flags():
        monkeypatch.setattr(issue_map.settings, flag, False, raising=False)
    monkeypatch.setattr("engine.ingestion.article_text.enrich_with_article_text",
                        lambda mentions: None)
    monkeypatch.setattr("engine.ingestion.socialcrawl_connector.SocialCrawlConnector", _Fake)


def _tier(monkeypatch, tier, reason="test"):
    monkeypatch.setattr(issue_map, "_social_tier", lambda: (tier, 100.0, reason))


def test_the_issue_map_uses_the_paid_backbone_when_credits_exist(monkeypatch):
    _tier(monkeypatch, "managed")
    report: dict = {}
    mentions = issue_map.acquire_intersection(
        "Okiya Omtatah", "IMF", None, None,
        identities=["Okiya Omtatah"], issue_terms=["IMF"], report=report)
    assert _Fake.calls, "the map never asked the paid backbone for anything"
    assert mentions
    assert report["tier"] == "managed"
    assert report["mentions"] == len(mentions)


def test_no_key_means_no_paid_call_and_no_failure(monkeypatch):
    _tier(monkeypatch, "free", "no SocialCrawl key configured")
    report: dict = {}
    mentions = issue_map.acquire_intersection(
        "Okiya Omtatah", "IMF", None, None,
        identities=["Okiya Omtatah"], issue_terms=["IMF"], report=report)
    assert _Fake.calls == []
    assert mentions == []
    assert report["tier"] == "free"


def test_credits_running_out_falls_back_instead_of_burning_the_run(monkeypatch):
    _tier(monkeypatch, "free", "SocialCrawl credits exhausted (0)")
    issue_map.acquire_intersection("P", "I", None, None,
                                   identities=["P"], issue_terms=["I"])
    assert _Fake.calls == []


def test_a_failing_paid_call_costs_its_own_query_only(monkeypatch):
    """A 402 mid-sweep must not end the acquisition."""
    _tier(monkeypatch, "managed")

    class _Boom(_Fake):
        def search_page(self, *a, **k):
            raise RuntimeError("402 Payment Required")

    monkeypatch.setattr("engine.ingestion.socialcrawl_connector.SocialCrawlConnector", _Boom)
    monkeypatch.setattr(issue_map.settings, "enable_reddit", True)
    monkeypatch.setattr("engine.ingestion.reddit_connector.RedditConnector", _Fake)

    mentions = issue_map.acquire_intersection(
        "P", "I", None, None, identities=["P"], issue_terms=["I"])
    assert mentions, "one paid failure took the whole sweep down"


def test_a_broken_tier_probe_is_never_fatal(monkeypatch):
    def _explode():
        raise RuntimeError("meta endpoint down")

    monkeypatch.setattr("engine.ingestion.orchestrator.resolve_social_tier", _explode)
    tier, balance, reason = issue_map._social_tier()
    assert tier == "free" and balance is None and "probe failed" in reason


def test_the_tier_is_resolved_live_so_paying_needs_no_redeploy():
    """resolve_social_tier probes the balance on every call and caches nothing;
    a top-up takes effect on the next run."""
    import inspect

    from engine.ingestion import orchestrator

    source = inspect.getsource(orchestrator.resolve_social_tier)
    assert "check_balance()" in source
    assert "cache" not in source.lower()


def test_the_paid_budget_is_smaller_than_the_free_ones():
    """Free calls cost time; paid calls cost money."""
    assert issue_map.PAIR_BUDGET["socialcrawl"] <= issue_map.PAIR_BUDGET["gdelt"]


def test_the_map_spends_no_more_than_its_budget_and_says_what_it_spent(monkeypatch):
    """The map called the connector's whole fetch per pair — a 50-credit
    composite and a comment call per post — with no ceiling. Each page's
    cost is now counted before it is spent."""
    from engine.ingestion import socialcrawl_connector as sc

    _tier(monkeypatch, "managed")
    monkeypatch.setattr(issue_map.settings, "issue_map_credit_budget", 10.0)
    report: dict = {}
    issue_map.acquire_intersection("Okiya Omtatah", "IMF", None, None,
                                   identities=["Okiya Omtatah"], issue_terms=["IMF", "International Monetary Fund"],
                                   report=report)
    assert report["credits_spent"] <= 10.0
    assert report["stopped"] == "the map's 10-credit budget was reached"
    assert report["calls"] == len(_Fake.calls)
    assert all("brand" not in path for path in issue_map.MAP_SOCIAL_ENDPOINTS)
    assert all(path in {p for _, p, _, _ in sc.SEARCH_ENDPOINTS} for path in issue_map.MAP_SOCIAL_ENDPOINTS)


def test_an_exhausted_account_stops_the_sweep_at_once(monkeypatch):
    _tier(monkeypatch, "managed")

    class _Empty(_Fake):
        credits_exhausted = True

    monkeypatch.setattr("engine.ingestion.socialcrawl_connector.SocialCrawlConnector", _Empty)
    report: dict = {}
    issue_map.acquire_intersection("P", "I", None, None, identities=["P"], issue_terms=["I"], report=report)
    assert _Fake.calls == [] and report["stopped"] == "the SocialCrawl account ran out of credits"
