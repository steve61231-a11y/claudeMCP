"""SocialCrawl is the social backbone; free scrapers take over on credit death.

The operating rule: run the paid social source whenever credits allow, and the
INSTANT they're exhausted switch to the free scrapers so a run still returns
social data instead of a pile of 402s. News/archive/web sources are additive and
must keep running either way.
"""

from datetime import datetime, timedelta

from engine.config import settings
from engine.db.models import IngestionTask, Politician
from engine.ingestion import orchestrator


def _connectors(db_session, politician) -> set[str]:
    now = datetime.utcnow()
    run = orchestrator.plan_run(db_session, politician, now - timedelta(days=7), now)
    rows = db_session.query(IngestionTask.connector).filter_by(run_id=run.id).all()
    return {c for (c,) in rows}, run


def _subject(db_session):
    p = Politician(name="Tier Probe", aliases=["Probe"], keywords=[])
    db_session.add(p)
    db_session.flush()
    return p


def test_managed_tier_used_when_credits_available(db_session, monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key", raising=False)
    monkeypatch.setattr(orchestrator, "resolve_social_tier", lambda: ("managed", 500.0, "ok"))

    connectors, run = _connectors(db_session, _subject(db_session))

    assert "socialcrawl" in connectors, "paid backbone must run when credits allow"
    assert run.stats["social_tier"] == "managed"


def test_zero_credits_switches_to_free_scrapers(db_session, monkeypatch):
    """The core requirement: no credits -> free social scrapers immediately."""
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "enable_twscrape", False, raising=False)
    monkeypatch.setattr(settings, "enable_scweet", False, raising=False)
    # The free X backends need a logged-in account to return anything.
    monkeypatch.setattr(settings, "x_username", "burner", raising=False)
    monkeypatch.setattr(settings, "x_password", "secret", raising=False)
    monkeypatch.setattr(
        orchestrator, "resolve_social_tier", lambda: ("free", 0.0, "SocialCrawl credits exhausted (0)")
    )

    connectors, run = _connectors(db_session, _subject(db_session))

    assert "socialcrawl" not in connectors, "must not burn the run on 402s"
    # Free social scrapers activate even though their flags are off.
    assert {"twscrape", "scweet"} <= connectors
    assert run.stats["social_tier"] == "free"
    assert run.stats["social_fallback_active"] is True
    assert "exhausted" in run.stats["social_tier_reason"]


def test_free_fallback_skipped_without_x_credentials(db_session, monkeypatch):
    """Without an X login the free backends can't return anything, so we don't
    schedule empty tasks — and we say why."""
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "enable_twscrape", False, raising=False)
    monkeypatch.setattr(settings, "enable_scweet", False, raising=False)
    monkeypatch.setattr(settings, "x_username", "", raising=False)
    monkeypatch.setattr(settings, "x_password", "", raising=False)
    monkeypatch.setattr(orchestrator, "resolve_social_tier", lambda: ("free", 0.0, "exhausted"))

    connectors, run = _connectors(db_session, _subject(db_session))

    assert not ({"twscrape", "scweet"} & connectors)
    assert run.stats["social_fallback_active"] is False
    assert "X credentials" in run.stats["social_fallback_reason"]


def test_news_and_web_sources_run_in_both_tiers(db_session, monkeypatch):
    """Social tier never gates news/archive/discovery — they're additive."""
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key", raising=False)
    expected = {"gdelt", "wikipedia", "google_news", "reddit", "youtube"}

    for tier in ("managed", "free"):
        monkeypatch.setattr(orchestrator, "resolve_social_tier", lambda t=tier: (t, 0.0, t))
        connectors, _ = _connectors(db_session, _subject(db_session))
        assert expected <= connectors, f"news/web sources missing in {tier} tier"


def test_resolve_social_tier_fails_open_when_balance_unknown(monkeypatch):
    """A transient probe failure must not silently downgrade the richest source."""
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key", raising=False)

    class Unreachable:
        def check_balance(self):
            return None

    import engine.ingestion.socialcrawl_connector as sc

    monkeypatch.setattr(sc, "SocialCrawlConnector", Unreachable)
    tier, balance, reason = orchestrator.resolve_social_tier()
    assert tier == "managed"
    assert balance is None
    assert "unknown" in reason


def test_resolve_social_tier_free_when_below_floor(monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "socialcrawl_min_credits", 5.0, raising=False)

    class LowBalance:
        def check_balance(self):
            return 0.5

    import engine.ingestion.socialcrawl_connector as sc

    monkeypatch.setattr(sc, "SocialCrawlConnector", LowBalance)
    tier, balance, reason = orchestrator.resolve_social_tier()
    assert tier == "free"
    assert balance == 0.5
    assert "exhausted" in reason


def test_no_key_configured_means_free_tier(monkeypatch):
    monkeypatch.setattr(settings, "socialcrawl_api_key", "", raising=False)
    tier, balance, reason = orchestrator.resolve_social_tier()
    assert tier == "free"
    assert "no SocialCrawl key" in reason
