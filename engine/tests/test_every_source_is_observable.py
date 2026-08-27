"""A connector that runs in production must be visible in the diagnostic.

Wayback was enabled by default, scheduled by plan_run on every single report,
and missing from the source-check probe table — so whether it returned anything
was unknowable from outside. That is exactly the blind spot that let the
discovery layer sit dead for weeks while the diagnostic said "0 results, check
the instance is up".
"""

import re
from pathlib import Path

from datetime import datetime, timedelta

from engine.ingestion.wayback_connector import WaybackConnector

API_SERVER = Path(__file__).resolve().parents[1] / "api_server.py"
ORCHESTRATOR = Path(__file__).resolve().parents[1] / "ingestion" / "orchestrator.py"


def _probed_connectors() -> set[str]:
    """Names source-check probes: the dict literal, plus the conditional ones
    added after it (the X backends are only registered when enabled)."""
    source = API_SERVER.read_text(encoding="utf-8")
    literal = source[source.index("    probes = {"):]
    literal = literal[: literal.index("\n    }")]
    names = set(re.findall(r'"([a-z_]+)": \(', literal))
    names |= set(re.findall(r'probes\["([a-z_]+)"\]\s*=', source))
    return names


def _scheduled_connectors() -> set[str]:
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    return set(re.findall(r'connector="([a-z_]+)"', source))


def test_wayback_is_probed():
    """The specific gap that prompted this."""
    assert "wayback" in _probed_connectors()


def test_every_free_connector_plan_run_schedules_can_be_probed():
    """Anything scheduled on a real run should be answerable by source-check.

    Excluded deliberately: paid/keyed backends and fixtures, which the probe
    reports through other means or which have no live endpoint to hit.
    """
    not_probeable = {
        "socialcrawl",   # paid, reported via the credit balance
        "newsapi",       # keyed; absent unless configured
        "agentreach",    # keyed
        "facebook",      # needs cookies
        "curated",       # local fixtures
        "mock",          # local fixtures
        "discovery",     # probed separately, with its resolved URL
        "issue_map",     # not a source; the intersection writer
    }
    # The two X backends are probed under `scweet_x` / `twscrape_x`; the suffix
    # marks the platform, not a different connector.
    probed = {name.removesuffix("_x") for name in _probed_connectors()}
    missing = _scheduled_connectors() - probed - not_probeable
    assert not missing, f"scheduled on every run but invisible in source-check: {sorted(missing)}"


def test_wayback_reports_why_it_found_nothing(monkeypatch):
    """A blocked host and an empty archive must not look identical."""
    connector = WaybackConnector(domains=["a.example", "b.example"])

    def boom(*a, **k):
        raise ConnectionError("Host not in allowlist: web.archive.org")

    monkeypatch.setattr("engine.ingestion.wayback_connector.http.get", boom)
    we = datetime(2026, 6, 1)
    assert connector.fetch("Rigathi Gachagua", [], we - timedelta(days=30), we) == []
    assert connector.last_error and "allowlist" in connector.last_error


def test_a_partial_sweep_is_not_reported_as_an_error(monkeypatch):
    """archive.org genuinely has no captures for some outlets. Calling that an
    error would cry wolf on every run."""
    connector = WaybackConnector(domains=["good.example", "bad.example"])
    calls = {"n": 0}

    def flaky(url, params=None, timeout=None, **k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ConnectionError("down")

        class _R:
            status_code = 200

            def json(self):
                return []

            @property
            def text(self):
                return "[]"

            def raise_for_status(self):
                pass

        return _R()

    monkeypatch.setattr("engine.ingestion.wayback_connector.http.get", flaky)
    we = datetime(2026, 6, 1)
    connector.fetch("Rigathi Gachagua", [], we - timedelta(days=30), we)
    assert connector.last_error is None
