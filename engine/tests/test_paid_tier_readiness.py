"""Turning on the paid social backbone must change every surface, and must
never be the thing that breaks a run.

The report path resolved the tier per run and scheduled SocialCrawl tasks; the
issue map swept four free news and forum sources and nothing else, so paying
for SocialCrawl would have improved the report and left the map exactly as it
was. The network is built from the database and improves for free — behind a
thirty-minute cache that would have shown the same graph for half an hour
after the money was spent.
"""

import pytest

from engine.reports import issue_map


class _Fake:
    """Stands in for whichever connector the sweep reaches for."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    def fetch(self, query, aliases, ws, we):
        _Fake.calls.append(query)
        return [{"text": f"from {query}", "raw_payload": {"url": f"https://x/{len(_Fake.calls)}"}}]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    _Fake.calls = []
    for flag in ("enable_gdelt", "enable_google_news", "enable_reddit", "enable_youtube"):
        monkeypatch.setattr(issue_map.settings, flag, False)
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
        def fetch(self, query, aliases, ws, we):
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


def test_a_finished_run_clears_the_cached_network():
    from engine import api_server

    api_server._network_cache["okiya omtatah"] = {"at": 0, "graph": {"stale": True}}
    api_server._invalidate_network("Okiya Omtatah")
    assert "okiya omtatah" not in api_server._network_cache
    # And an unknown name is not an error.
    api_server._invalidate_network("nobody at all")
