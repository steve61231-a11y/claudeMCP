"""A source that failed must not read as a source with nothing to say.

Searching a real politician and getting two mentions looks like an honest
answer about a quiet subject. It is equally consistent with every connector
being refused by its host and returning [] without a word. These tests pin the
contract that tells the two apart.
"""

import inspect
import importlib
import pkgutil

import pytest

import engine.ingestion as ingestion_pkg
from engine.ingestion.base import IngestionConnector


def _connector_classes():
    found = []
    for module in pkgutil.iter_modules(ingestion_pkg.__path__):
        if not module.name.endswith("_connector"):
            continue
        try:
            loaded = importlib.import_module(f"engine.ingestion.{module.name}")
        except Exception:  # optional dependency absent in this environment
            continue
        for name, obj in inspect.getmembers(loaded, inspect.isclass):
            if obj.__module__ == loaded.__name__ and name.endswith("Connector"):
                found.append((module.name, obj))
    return found


def test_the_base_contract_declares_last_error():
    assert IngestionConnector.last_error is None, (
        "getattr(connector, 'last_error') must be meaningful for every connector, "
        "including ones that never set it")


@pytest.mark.parametrize("module_name,cls",
                         _connector_classes(),
                         ids=lambda v: v if isinstance(v, str) else v.__name__)
def test_every_connector_can_report_why_it_returned_nothing(module_name, cls):
    assert hasattr(cls, "last_error") or "last_error" in inspect.getsource(cls), (
        f"{module_name}.{cls.__name__} returns [] on failure with no way to say why")


# --- the specific connectors that were silent --------------------------------

def _fetch_with_dead_network(connector, monkeypatch, module):
    def boom(*a, **k):
        raise ConnectionError("host refused the connection")

    monkeypatch.setattr(module.http, "get", boom)
    from datetime import datetime
    return connector.fetch("Stephen Kalonzo", [], datetime(2026, 1, 1), datetime(2026, 8, 1))


def test_google_news_records_a_refused_host(monkeypatch):
    from engine.ingestion import google_news_rss_connector as gn

    connector = gn.GoogleNewsRssConnector()
    assert _fetch_with_dead_network(connector, monkeypatch, gn) == []
    assert connector.last_error, "returned nothing and said nothing about why"
    assert "ConnectionError" in connector.last_error


def test_reddit_records_a_refused_host(monkeypatch):
    from engine.ingestion import reddit_connector as rc

    connector = rc.RedditConnector()
    assert _fetch_with_dead_network(connector, monkeypatch, rc) == []
    assert connector.last_error and "ConnectionError" in connector.last_error


def test_gdelt_records_a_refused_host(monkeypatch):
    from engine.ingestion import gdelt_connector as gc

    connector = gc.GdeltConnector()
    assert _fetch_with_dead_network(connector, monkeypatch, gc) == []
    assert connector.last_error


def test_wikipedia_records_a_refused_host(monkeypatch):
    from engine.ingestion import wikipedia_connector as wc

    connector = wc.WikipediaConnector()
    _fetch_with_dead_network(connector, monkeypatch, wc)
    assert connector.last_error


def test_a_healthy_fetch_leaves_no_error(monkeypatch):
    from datetime import datetime
    from engine.ingestion import google_news_rss_connector as gn

    class Resp:
        status_code = 200
        content = b"<rss><channel></channel></rss>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(gn.http, "get", lambda *a, **k: Resp())
    connector = gn.GoogleNewsRssConnector()
    connector.fetch("X", [], datetime(2026, 1, 1), datetime(2026, 8, 1))
    assert not connector.last_error, "a successful empty result is not an error"


# --- the coverage panel must not call a blocked source "delivered" -----------

def _summary(health):
    from engine.pipeline import _coverage_summary

    return _coverage_summary({"source_health": health})


def test_a_source_that_completed_with_nothing_is_not_delivered():
    """"succeeded == attempted" was true for a connector the host refused, so
    a run where Reddit and X were blocked printed "Every enabled source
    delivered this run"."""
    result = _summary({"reddit": {"attempted": 1, "succeeded": 1, "results": 0,
                                  "failures": {"silent_empty": 1},
                                  "errors": ["returned nothing: ConnectionError: refused"]}})
    assert result["sources_down"] == ["reddit"]
    assert result["sources_ok"] == []
    assert result["complete"] is False


def test_the_note_says_what_the_host_actually_did():
    result = _summary({"reddit": {"attempted": 1, "succeeded": 1, "results": 0,
                                  "failures": {"silent_empty": 1},
                                  "errors": ["returned nothing: ConnectionError: refused"]}})
    assert result["notes"] == ["reddit: returned nothing — ConnectionError: refused"]


def test_a_source_that_genuinely_delivered_is_still_ok():
    result = _summary({"google_news": {"attempted": 2, "succeeded": 2, "results": 80,
                                       "failures": {}}})
    assert result["sources_ok"] == ["google_news"]
    assert result["complete"] is True


def test_a_source_with_no_results_and_no_error_is_not_condemned():
    """Zero results with nothing recorded is a genuinely quiet source, not a
    failure — we must not invent a fault we did not observe."""
    result = _summary({"wikipedia": {"attempted": 1, "succeeded": 1, "results": 0,
                                     "failures": {}}})
    assert result["sources_ok"] == ["wikipedia"]
