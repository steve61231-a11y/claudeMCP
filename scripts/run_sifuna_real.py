"""One-off real-pipeline run for Edwin Sifuna against curated real-world data.

Neo4j isn't reachable in this sandbox (no docker registry egress, not
apt-installable), so the graph layer is faked the same way engine/tests/
test_pipeline.py does it -- everything else (Postgres, the curated real
mentions, and live Anthropic LLM calls) is real.
"""
import json
from datetime import datetime

from engine.db.session import SessionLocal
from engine.db.models import Politician, IntelligenceReport
from engine.intelligence import graph as graph_module


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


fake_driver = FakeDriver()
graph_module.get_driver = lambda: fake_driver
graph_module.get_network_snapshot = lambda politician_id, limit=50: {
    "politician_id": politician_id,
    "top_users": [],
}

from engine.pipeline import run_pipeline  # noqa: E402  (import after monkeypatch)

db = SessionLocal()
try:
    politician = db.query(Politician).filter_by(name="Edwin Sifuna").first()
    if not politician:
        politician = Politician(
            name="Edwin Sifuna",
            aliases=["Sifuna", "Sen. Sifuna", "Edwin Ochieng Sifuna"],
            keywords=["ODM", "Nairobi Senator", "secretary-general"],
        )
        db.add(politician)
        db.commit()

    report = run_pipeline(
        db,
        politician,
        period="real-pilot",
        window_start=datetime(2025, 12, 1),
        window_end=datetime(2026, 6, 23),
    )
    print("REPORT_ID:", report.id)
    print(json.dumps(report.payload, indent=2, default=str))
finally:
    db.close()
