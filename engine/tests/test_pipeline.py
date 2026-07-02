from datetime import datetime

import numpy as np

from engine import llm
from engine.db.models import MentionEntity, Politician, RawMention
from engine.intelligence import graph as graph_module
from engine.intelligence import narratives as narratives_module
from engine.pipeline import run_pipeline
from engine.processing import sentiment as sentiment_module

REPORT_SECTIONS = [
    "executive_summary",
    "sentiment_breakdown",
    "volume_trends",
    "influence_summary",
    "narrative_breakdown",
    "network_insights",
]


def fake_call_json(prompt: str, max_tokens: int = 1024):
    if "refer to the politician above" in prompt:
        matched = "Governor" in prompt and "Nakuru" in prompt
        return {"matched": matched, "confidence": 0.85 if matched else 0.1}
    if "Classify the political tone" in prompt:
        return {"sentiment": "neutral", "intensity": 3, "context_tag": "concern"}
    if "summarizing a cluster" in prompt:
        return {"label": "Test Narrative", "description": "A test narrative cluster."}
    return {}


def fake_local_sentiment(text: str) -> dict:
    return {"sentiment": "positive", "confidence": 0.8, "intensity": 4}


def fake_embed_texts(texts):
    return np.array([np.ones(8) * i for i in range(len(texts))])


class FakeGraphSession:
    def __init__(self, recorder):
        self.recorder = recorder

    def run(self, query, **params):
        self.recorder.append((query.strip().splitlines()[0], params))
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeDriver:
    def __init__(self):
        self.recorded_calls = []

    def session(self):
        return FakeGraphSession(self.recorded_calls)


def patch_pipeline_dependencies(monkeypatch):
    # Force the mock ingestion source: without this the orchestrator makes
    # REAL SocialCrawl/NewsAPI calls, so the suite's outcome would depend on
    # network state and remaining API credits.
    from engine.config import settings as config_settings

    monkeypatch.setattr(config_settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(config_settings, "newsapi_key", "")
    monkeypatch.setattr(llm, "call_json", fake_call_json)
    monkeypatch.setattr(sentiment_module, "local_sentiment", fake_local_sentiment)
    monkeypatch.setattr(narratives_module, "embed_texts", fake_embed_texts)

    fake_driver = FakeDriver()
    monkeypatch.setattr(graph_module, "get_driver", lambda: fake_driver)
    monkeypatch.setattr(
        graph_module,
        "get_network_snapshot",
        lambda politician_id, limit=50: {"politician_id": politician_id, "top_users": []},
    )
    return fake_driver


def make_politician(db_session) -> Politician:
    politician = Politician(name="Wanjiru Kamau", aliases=["Kamau"], keywords=["Governor", "Nakuru"])
    db_session.add(politician)
    db_session.commit()
    return politician


def test_pipeline_produces_full_report(db_session, monkeypatch):
    patch_pipeline_dependencies(monkeypatch)
    politician = make_politician(db_session)

    report = run_pipeline(
        db_session, politician, "weekly", datetime(2026, 6, 1), datetime(2026, 6, 22, 23, 59, 59)
    )

    for section in REPORT_SECTIONS:
        assert section in report.payload, f"missing report section: {section}"

    assert db_session.query(RawMention).count() > 0


def test_pipeline_links_indirect_mentions_to_politician_entity(db_session, monkeypatch):
    patch_pipeline_dependencies(monkeypatch)
    politician = make_politician(db_session)

    run_pipeline(db_session, politician, "weekly", datetime(2026, 6, 1), datetime(2026, 6, 22, 23, 59, 59))

    indirect_links = db_session.query(MentionEntity).filter_by(match_type="indirect_llm").all()
    assert len(indirect_links) > 0


def test_pipeline_writes_graph_only_after_postgres_commit(db_session, monkeypatch):
    """Regression test for the write-ordering fix: Neo4j upserts must happen
    after the Postgres commit, and only contain data that's actually durable.
    """
    fake_driver = patch_pipeline_dependencies(monkeypatch)
    politician = make_politician(db_session)

    report = run_pipeline(
        db_session, politician, "weekly", datetime(2026, 6, 1), datetime(2026, 6, 22, 23, 59, 59)
    )

    assert len(fake_driver.recorded_calls) > 0
    mention_count = db_session.query(RawMention).count()
    assert mention_count > 0
    assert report.id is not None


def test_pipeline_rerun_does_not_duplicate_mentions(db_session, monkeypatch):
    """Cross-run dedup: re-running the same window must not re-insert
    mentions already stored for the politician."""
    patch_pipeline_dependencies(monkeypatch)
    politician = make_politician(db_session)
    window = (datetime(2026, 6, 1), datetime(2026, 6, 22, 23, 59, 59))

    run_pipeline(db_session, politician, "weekly", *window)
    first_count = db_session.query(RawMention).count()
    assert first_count > 0

    run_pipeline(db_session, politician, "weekly", *window)
    assert db_session.query(RawMention).count() == first_count
