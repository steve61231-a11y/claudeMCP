"""A full pipeline run on the stub backend — no network, no cost.

This is the test that makes stub mode worth having. The unit tests prove the
stub returns canned JSON; only a real run proves the pipeline survives a model
that answers nothing useful, and that the report it produces is clearly labelled
as not being real analysis. Both are load-bearing: the first is what lets us
test connectors, the database, orchestration and the frontend for free, and the
second is what stops a free run being handed to a client.
"""

from datetime import datetime

from engine import llm
from engine.db.models import RawMention
from engine.pipeline import run_pipeline
from engine.tests.test_pipeline import (
    REPORT_SECTIONS,
    fake_embed_texts,
    fake_local_sentiment,
    make_politician,
)


def _stub_pipeline(monkeypatch):
    """Same hermetic setup as the main pipeline tests, but the LLM layer is NOT
    monkeypatched — calls go through the real stub backend."""
    from engine.config import settings as config_settings
    from engine.intelligence import graph as graph_module
    from engine.intelligence import narratives as narratives_module
    from engine.processing import sentiment as sentiment_module
    from engine.tests.test_pipeline import FakeDriver

    monkeypatch.setattr(config_settings, "llm_provider", "stub")
    monkeypatch.setattr(config_settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(config_settings, "newsapi_key", "")
    for flag in ("enable_gdelt", "enable_wayback", "enable_wikipedia",
                 "enable_google_news", "enable_reddit", "enable_youtube"):
        monkeypatch.setattr(config_settings, flag, False, raising=False)
    monkeypatch.setattr(sentiment_module, "local_sentiment", fake_local_sentiment)
    monkeypatch.setattr(narratives_module, "embed_texts", fake_embed_texts)

    # Any attempt to reach Anthropic is a bug: stub mode must cost nothing.
    def _no_network(*args, **kwargs):
        raise AssertionError("stub mode reached the Anthropic API")

    monkeypatch.setattr(llm, "get_client", _no_network)

    fake_driver = FakeDriver()
    monkeypatch.setattr(graph_module, "get_driver", lambda: fake_driver)
    monkeypatch.setattr(
        graph_module, "get_network_snapshot",
        lambda politician_id, limit=50: {"politician_id": politician_id, "top_users": []},
    )


def test_a_full_run_completes_on_the_stub_backend(db_session, monkeypatch):
    """The point of stub mode: exercise everything around the model for free."""
    _stub_pipeline(monkeypatch)
    politician = make_politician(db_session)

    report = run_pipeline(
        db_session, politician, "weekly",
        datetime(2026, 6, 1), datetime(2026, 6, 22, 23, 59, 59),
    )

    for section in REPORT_SECTIONS:
        assert section in report.payload, f"missing report section: {section}"
    assert db_session.query(RawMention).count() > 0, "ingestion did not run"


def test_the_run_is_labelled_test_grade(monkeypatch):
    """A free run must not be mistakable for a client deliverable."""
    monkeypatch.setattr(llm.settings, "llm_provider", "stub")

    grade = llm.report_grade()

    assert grade["production"] is False
    assert grade["backend"] == "stub"
    assert "must not be shown to a client" in grade["warning"]


# --- startup credentials ----------------------------------------------------

def _settings(**overrides):
    from engine.config import Settings

    base = dict(app_env="production", anthropic_api_key="", internal_api_token="t",
                database_url="postgresql://x/y", neo4j_password="p")
    base.update(overrides)
    return Settings(**base)


def test_stub_mode_needs_no_api_key():
    """A zero-cost mode that still demands a paid credential is not zero-cost."""
    _settings(llm_provider="stub").validate_for_startup()


def test_anthropic_mode_still_requires_its_key():
    import pytest

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _settings(llm_provider="anthropic").validate_for_startup()


def test_openai_compatible_names_exactly_what_is_missing():
    import pytest

    with pytest.raises(RuntimeError) as excinfo:
        _settings(llm_provider="openai_compatible").validate_for_startup()

    message = str(excinfo.value)
    for required in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        assert required in message
    assert "ANTHROPIC_API_KEY" not in message  # not needed on this provider
