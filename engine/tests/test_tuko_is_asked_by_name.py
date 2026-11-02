"""Kenyan newsrooms are asked by name — Tuko first.

Founder, 2 Nov 2026: add Tuko as one of Mũũgĩ's sources. The probe
(scripts/tuko_probe.py, from GitHub's network) found Tuko already the
largest Kenyan newsroom in the corpus by name — through Google News's
general results, where one query returns the hundred best matches across
every outlet and a newsroom that writes often about a subject can be
crowded out. Its own search page returns the same links whatever is asked,
and a `site:tuko.co.ke` Google News query returns its stories on the
subject, dated, for the period. So each newsroom in KENYAN_NEWSROOM_SITES
is asked by name in both products, through the same connector — the period
filter, the link resolver and the full-article reader apply unchanged.
"""

from datetime import datetime

from engine import config
from engine.ingestion import google_news_rss_connector
from engine.reports import issue_map


def test_the_setting_lists_bare_domains(monkeypatch):
    monkeypatch.setattr(config.settings, "kenyan_newsroom_sites", " www.tuko.co.ke, kenyans.co.ke ,")
    assert config.newsroom_sites() == ["tuko.co.ke", "kenyans.co.ke"]
    monkeypatch.setattr(config.settings, "kenyan_newsroom_sites", "")
    assert config.newsroom_sites() == []


def test_tuko_is_a_default_source():
    assert "tuko.co.ke" in config.Settings().kenyan_newsroom_sites


def test_the_search_report_asks_each_newsroom_for_the_subject():
    src = open(issue_map.__file__.replace("reports/issue_map.py", "ingestion/orchestrator.py")).read()
    assert "query=f'site:{site} \"{politician.name}\"'" in src


def test_the_issue_map_asks_each_newsroom_for_the_pair_and_the_issue(monkeypatch):
    asked = []

    class Fake:
        def fetch(self, name, aliases, ws, we, query=None):
            asked.append(query)
            return []

    monkeypatch.setattr(google_news_rss_connector, "GoogleNewsRssConnector", Fake)
    from engine.tests.conftest import source_flags

    for flag in source_flags():
        monkeypatch.setattr(issue_map.settings, flag, False)
    monkeypatch.setattr(issue_map.settings, "enable_google_news", True)
    monkeypatch.setattr(issue_map.settings, "kenyan_newsroom_sites", "tuko.co.ke")
    monkeypatch.setattr(issue_map, "_social_tier", lambda: ("free", None, "test"))
    issue_map.acquire_intersection("Ministry of Health", "SHA", datetime(2026, 10, 1), datetime(2026, 10, 31),
                                   identities=["Ministry of Health"], issue_terms=["SHA", "Social Health Authority"])
    tuko = [q for q in asked if q and q.startswith("site:tuko.co.ke")]
    assert 'site:tuko.co.ke "Ministry of Health" "SHA"' in tuko
    assert 'site:tuko.co.ke "SHA"' in tuko, "the issue alone, inside the newsroom"
