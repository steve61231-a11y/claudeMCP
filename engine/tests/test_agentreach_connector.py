from datetime import datetime

import pytest

# AgentReach is an OPTIONAL, off-by-default scraping backend (enable_agentreach)
# that shells out to the external `agent_reach` package. These tests monkeypatch
# that module, so skip cleanly when it isn't installed instead of hard-failing.
pytest.importorskip("agent_reach")

from engine.ingestion.agentreach_connector import AgentReachConnector

SEARCH_MD = """
# News results

[Mbadi defends 4.8 trillion budget in Parliament](https://nation.africa/kenya/news/mbadi-budget-123)
Some Bing chrome [Sign in](https://www.bing.com/account)
[CS Mbadi clashes with Sifuna over ODM control](https://the-star.co.ke/news/mbadi-sifuna-456)
![img](https://img.example/x.png)
"""

ARTICLE_MD = """
# Mbadi defends budget

Treasury CS **John Mbadi** told MPs the 4.8 trillion shilling budget protects
essential services. [related](https://nation.africa/more)
"""


def test_web_search_maps_articles(monkeypatch):
    conn = AgentReachConnector(doctor={})  # no CLI backends

    class FakeWeb:
        def read(self, url):
            return ARTICLE_MD if "nation.africa/kenya" in url or "the-star" in url else SEARCH_MD

    monkeypatch.setattr("agent_reach.channels.web.WebChannel", FakeWeb)
    mentions = conn.fetch("Mbadi", [], datetime(2026, 6, 1), datetime(2026, 7, 1))

    urls = {m["raw_payload"]["url"] for m in mentions}
    assert "https://nation.africa/kenya/news/mbadi-budget-123" in urls
    assert "https://the-star.co.ke/news/mbadi-sifuna-456" in urls
    # Bing chrome link is filtered out
    assert not any("bing.com" in u for u in urls)
    # article body flows into the mention text
    first = next(m for m in mentions if "nation.africa/kenya" in m["raw_payload"]["url"])
    assert "John Mbadi" in first["text"]
    assert first["platform"] == "nation.africa"
    assert first["source_type"] == "article"


def test_web_search_dedupes_across_terms(monkeypatch):
    conn = AgentReachConnector(doctor={})

    class FakeWeb:
        def read(self, url):
            return SEARCH_MD if "bing.com" in url else ARTICLE_MD

    monkeypatch.setattr("agent_reach.channels.web.WebChannel", FakeWeb)
    mentions = conn.fetch("Mbadi", ["John Mbadi", "CS Mbadi"], datetime(2026, 6, 1), datetime(2026, 7, 1))
    urls = [m["raw_payload"]["url"] for m in mentions]
    assert len(urls) == len(set(urls))  # 3 aliases, same results, deduped


def test_twitter_backend_skipped_when_not_ok(monkeypatch):
    # twitter reported 'warn' (installed, not logged in) -> must not be called
    conn = AgentReachConnector(doctor={"twitter": {"status": "warn"}})
    called = []
    monkeypatch.setattr(conn, "_twitter_search", lambda *a, **k: called.append(1) or [])

    class FakeWeb:
        def read(self, url):
            return ""

    monkeypatch.setattr("agent_reach.channels.web.WebChannel", FakeWeb)
    conn.fetch("Mbadi", [], datetime(2026, 6, 1), datetime(2026, 7, 1))
    assert called == []


def test_twitter_backend_used_when_ok(monkeypatch):
    conn = AgentReachConnector(doctor={"twitter": {"status": "ok"}})
    monkeypatch.setattr(
        conn, "_run",
        lambda cmd: '[{"text": "Mbadi must explain the budget", "author": "citizen", "likes": 10}]',
    )
    mentions = conn._twitter_search("Mbadi", datetime(2026, 7, 1))
    assert mentions[0]["platform"] == "twitter"
    assert mentions[0]["author_handle"] == "citizen"
    assert mentions[0]["engagement"]["likes"] == 10
