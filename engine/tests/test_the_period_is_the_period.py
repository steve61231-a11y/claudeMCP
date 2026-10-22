"""If it's one year, we do one year. If it's one month, we do one month.

The founder, on what a Search report collects. Measured on 22 October 2026
from GitHub's network (`scripts/period_probe.py`):

  * a one-year Google News query (`when:365d`) returned 100 items, 97 of
    them from the last five months — "a year" was really a season;
  * the same query sliced by month with `after:` / `before:` returned only
    that month's items, slice after slice;
  * GDELT reaches back about three months whatever start date it is given.

So a long period is asked month by month as well as whole, GDELT's reach is
declared rather than mistaken for a quiet past, and the archive — which
carries no publication dates — is left to the Issue Map.
"""

from datetime import datetime, timedelta

from engine.config import settings
from engine.db.models import IngestionTask
from engine.ingestion import orchestrator
from engine.ingestion.google_news_rss_connector import GoogleNewsRssConnector

END = datetime(2026, 10, 22, 12)


def test_a_month_is_asked_whole():
    assert orchestrator.period_slices(END - timedelta(days=30), END) == []


def test_a_year_is_asked_month_by_month_with_nothing_missed():
    start = END - timedelta(days=365)
    slices = orchestrator.period_slices(start, END)
    assert len(slices) == 13
    assert slices[0][0] == start.date() and slices[-1][1] == END.date()
    for (_, end), (next_start, _) in zip(slices, slices[1:]):
        assert end == next_start, "a gap between slices is a month nobody asked about"


def test_a_slice_keeps_its_own_dates(monkeypatch):
    asked = []

    class _Resp:
        content = b"<rss><channel></channel></rss>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr("engine.ingestion.google_news_rss_connector.http.get",
                        lambda url, **kw: asked.append(url) or _Resp())
    connector = GoogleNewsRssConnector()
    connector.fetch("Martha Karua", [], END - timedelta(days=365), END,
                    query='"Martha Karua" after:2026-01-01 before:2026-01-31')
    connector.fetch("Martha Karua", [], END - timedelta(days=30), END, query='"Martha Karua"')
    assert "when%3A" not in asked[0], "when: on a slice cuts it to the period's last days"
    assert "when%3A30d" in asked[1]


def _plan(db_session, days, monkeypatch):
    from engine.tests.test_orchestrator import make_politician

    monkeypatch.setattr(settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, END - timedelta(days=days), END)
    queries = [t.query for t in db_session.query(IngestionTask).filter(
        IngestionTask.run_id == run.id, IngestionTask.connector == "google_news")]
    return run, queries


def test_a_year_report_plans_every_month(db_session, monkeypatch):
    monkeypatch.setattr(settings, "enable_google_news", True, raising=False)
    monkeypatch.setattr(settings, "enable_gdelt", True, raising=False)
    run, queries = _plan(db_session, 365, monkeypatch)
    slices = [q for q in queries if "after:" in q and "before:" in q]
    assert len(slices) == 13
    assert "three months" in run.stats["source_reach"]["gdelt"]


def test_a_month_report_plans_no_slices(db_session, monkeypatch):
    monkeypatch.setattr(settings, "enable_google_news", True, raising=False)
    monkeypatch.setattr(settings, "enable_gdelt", True, raising=False)
    run, queries = _plan(db_session, 30, monkeypatch)
    assert not [q for q in queries if "after:" in q]
    assert "source_reach" not in run.stats
