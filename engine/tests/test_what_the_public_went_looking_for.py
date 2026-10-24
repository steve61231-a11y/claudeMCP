"""What the public went looking for — search demand beside the coverage.

The founder, 24 October 2026: "I'd like to see what people are searching
about in that specific topic … in Kenya, and the deep one … and if people
outside Kenya are searching about it, that would be fantastic" — and, on
SocialCrawl: "we can't depend on it for everything … if it breaks down, our
whole app breaks down."

So: Google Trends for Kenya and the world, read against the coverage (who
led), the record (what the public asks that nothing collected answers) and
the storylines; Google's suggestions and Wikipedia's readers beside it; and
a ladder of providers, SocialCrawl first, Google directly second, each answer
saying which rung gave it. Each test pins one way the section could say
more than was measured.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from engine.evidence import search_demand as sd
from engine.evidence import search_sources
from engine.reports import from_facts
from engine.tests.test_end_to_end_run import scripted_model  # noqa: F401 — the fixture, shared

NOW = datetime(2026, 10, 24, 12, 0)
START, END = NOW - timedelta(days=30), NOW


# --- the window ---------------------------------------------------------------

def test_the_smallest_window_that_reaches_the_period_is_asked():
    assert sd.timeframe_for(START, END, NOW)[0] == "past_30_days"
    assert sd.timeframe_for(NOW - timedelta(days=7), END, NOW)[0] == "past_7_days"
    assert sd.timeframe_for(NOW - timedelta(hours=20), END, NOW)[0] == "past_day"
    assert sd.timeframe_for(NOW - timedelta(days=200), END, NOW)[0] == "past_12_months"


def test_a_period_that_ended_long_ago_is_not_described_by_a_window_ending_today():
    preset, _label, reason = sd.timeframe_for(NOW - timedelta(days=90), NOW - timedelta(days=40), NOW)
    assert preset is None and "cannot be read on its own" in reason


# --- three states, never two ---------------------------------------------------

def test_too_few_searches_is_a_finding_and_a_failure_is_not():
    assert sd._classify(404, "", None) == ("too_little_search", None)
    state, reason = sd._classify(402, "INSUFFICIENT_CREDITS", None)
    assert state == "not_measured" and "SOCIALCRAWL_API_KEY" in reason and "Nothing else" in reason
    assert sd._classify(None, "", "ConnectTimeout: x")[0] == "not_measured"
    assert sd._classify(503, "busy", None)[0] == "not_measured"


def _series(keyword, values, start=START):
    return {"keyword": keyword, "points": [
        {"date": (start + timedelta(days=i)).date().isoformat(),
         "datetime": (start + timedelta(days=i)).strftime("%Y-%m-%dT00:00:00.000Z"),
         "value": v, "partial": False} for i, v in enumerate(values)]}


def _answers(explore=None, rising=None, explore_state="measured", rising_state="measured"):
    calls = []

    def asker(api_key, kind, params):
        calls.append((kind, params))
        if kind == "explore":
            return {"state": explore_state, "reason": None if explore_state != "not_measured" else "down",
                    "data": explore, "provider": "test"}
        return {"state": rising_state, "reason": None, "data": rising, "provider": "test"}

    return asker, calls


def test_the_more_searched_name_is_the_one_expanded():
    explore = {"series": [_series("Jane Wanjiru", [1] * 31), _series("Wanjiru", [5] * 31)],
               "averages": [{"keyword": "Jane Wanjiru", "value": 3}, {"keyword": "Wanjiru", "value": 41}]}
    asker, calls = _answers(explore, {"top": [], "rising": []})
    raw = sd.fetch("Jane Wanjiru", ["Wanjiru"], START, END, "key", "KE", NOW, asker)
    assert raw["keyword"] == "Wanjiru"
    assert calls[1] == ("rising", {"keyword": "Wanjiru", "timeframe": "past_30_days", "location": "KE"})


def test_worldwide_is_asked_without_a_location():
    asker, calls = _answers({"series": [], "averages": []}, {"top": [], "rising": []})
    sd.fetch("Jane Wanjiru", [], START, END, "key", "", NOW, asker)
    assert all("location" not in params for _kind, params in calls)


# --- no single provider holds it up -------------------------------------------

def test_when_socialcrawl_cannot_answer_google_is_asked_and_the_page_is_told():
    paid = lambda key, kind, params: {"state": "not_measured", "reason": "no credits", "data": None}
    free = lambda key, kind, params: {"state": "measured", "reason": None, "data": {"top": [], "rising": []}}
    answer = search_sources.laddered(paid, free)("key", "rising", {"keyword": "x"})
    assert answer["provider"] == "Google Trends (direct)"
    assert answer["fallback_from"] == {"provider": "SocialCrawl", "reason": "no credits"}


def test_too_few_searches_from_the_first_provider_is_not_second_guessed():
    asked = []
    paid = lambda key, kind, params: {"state": "too_little_search", "reason": None, "data": None}
    free = lambda key, kind, params: asked.append(1) or {"state": "measured", "data": {}}
    answer = search_sources.laddered(paid, free)("key", "explore", {"keywords": "x"})
    assert answer["state"] == "too_little_search" and not asked


def test_without_a_key_socialcrawl_is_not_asked():
    paid = lambda *a: (_ for _ in ()).throw(AssertionError("asked without a key"))
    free = lambda key, kind, params: {"state": "measured", "reason": None, "data": {"top": [], "rising": []}}
    assert search_sources.laddered(paid, free)("", "rising", {"keyword": "x"})["state"] == "measured"


class _Response:
    def __init__(self, status, text="", payload=None):
        self.status_code, self.text = status, text
        self.content = text.encode()
        self._payload = payload

    def json(self):
        return self._payload if self._payload is not None else json.loads(self.text)


def test_google_trends_asked_directly_is_read_into_the_same_shape(monkeypatch):
    from engine.ingestion import http

    monkeypatch.setattr(search_sources, "_warmed", True)
    explore = ")]}'\n" + json.dumps({"widgets": [
        {"id": "TIMESERIES", "token": "t1", "request": {"r": 1}},
        {"id": "RELATED_QUERIES", "token": "t2", "request": {"r": 2}}]})
    multiline = ")]}',\n" + json.dumps({"default": {"timelineData": [
        {"time": "1760918400", "value": [40], "hasData": [True]},
        {"time": "1761004800", "value": [100], "hasData": [True], "isPartial": True}]}})
    related = ")]}',\n" + json.dumps({"default": {"rankedList": [
        {"rankedKeyword": [{"query": "wanjiru senate", "value": 100}]},
        {"rankedKeyword": [{"query": "wanjiru treasury", "value": 6150, "formattedValue": "Breakout"},
                           {"query": "wanjiru speech", "value": 250, "formattedValue": "+250%"}]}]}})

    def fake_get(url, params=None, **kw):
        if url.endswith("/explore"):
            return _Response(200, explore)
        return _Response(200, multiline if "multiline" in url else related)

    monkeypatch.setattr(http, "get", fake_get)
    timeline = search_sources.direct_ask("", "explore", {"keywords": "Wanjiru", "timeframe": "past_30_days",
                                                         "location": "KE"})
    assert timeline["state"] == "measured"
    assert [p["value"] for p in timeline["data"]["series"][0]["points"]] == [40, 100]
    queries = search_sources.direct_ask("", "rising", {"keyword": "Wanjiru", "timeframe": "past_30_days"})
    top, rising = sd._queries(queries["data"])
    assert top == [{"query": "wanjiru senate", "value": 100.0}]
    assert rising[0] == {"query": "wanjiru treasury", "growth": None, "breakout": True}, \
        "a breakout is a flag, never a number nobody measured"
    assert rising[1]["growth"] == 250.0


def test_a_refusal_from_google_is_remembered_not_rediscovered(monkeypatch):
    from engine.ingestion import http

    monkeypatch.setattr(search_sources, "_warmed", True)
    monkeypatch.setattr(http, "get", lambda url, **kw: _Response(429, "rate limited"))
    answer = search_sources.direct_ask("", "explore", {"keywords": "x", "timeframe": "past_7_days"})
    assert answer["state"] == "not_measured" and "429" in answer["reason"]
    assert http.refused(search_sources.TRENDS_API + "/explore")


def test_googles_suggestions_are_read_and_deduplicated(monkeypatch):
    from engine.ingestion import http

    def fake_get(url, params=None, **kw):
        q = params["q"]
        return _Response(200, json.dumps([q, [q, "jane wanjiru husband", "jane wanjiru age"]]))

    monkeypatch.setattr(http, "get", fake_get)
    got = search_sources.suggestions("jane wanjiru", "KE")
    assert got["state"] == "measured"
    assert [s["query"] for s in got["suggestions"]] == ["jane wanjiru husband", "jane wanjiru age"]


def test_suggestions_that_could_not_be_asked_are_not_an_empty_list(monkeypatch):
    from engine.ingestion import http

    monkeypatch.setattr(http, "get", lambda url, **kw: _Response(403, "no"))
    got = search_sources.suggestions("jane wanjiru", "KE")
    assert got["state"] == "not_measured" and "403" in got["reason"]


def test_wikipedia_readers_are_counted_from_the_article_wikipedia_ranks_first(monkeypatch):
    from engine.ingestion import http
    from engine.ingestion.wikipedia_connector import WikipediaConnector

    monkeypatch.setattr(WikipediaConnector, "_resolve_title", lambda self, n, a: "Jane Wanjiru")
    items = [{"timestamp": "2026101400", "views": 120}, {"timestamp": "2026101500", "views": 900}]
    monkeypatch.setattr(http, "get", lambda url, **kw: _Response(200, payload={"items": items}))
    got = search_sources.pageviews("Jane Wanjiru", [], START, END)
    assert got["total"] == 1020 and got["url"].endswith("/Jane_Wanjiru")


# --- the reading ----------------------------------------------------------------

DAYS = [(START + timedelta(days=i)).date().isoformat() for i in range(31)]


def _lead_series():
    """Searches spike on day 10 and 20; coverage follows a day later."""
    search = [10.0] * 31
    coverage = [2] * 31
    for d in (10, 20):
        search[d] = 100.0
        coverage[d + 1] = 30
    return search, coverage


def test_a_search_rise_before_the_coverage_reads_as_the_public_leading():
    search, coverage = _lead_series()
    ll = sd.lead_lag(dict(zip(DAYS, search)), dict(zip(DAYS, coverage)), DAYS)
    assert ll["reading"] == "search_led" and ll["lag_days"] == 1


def test_noise_is_not_read_as_a_lead():
    import random

    rng = random.Random(4)
    s = {d: rng.random() * 100 for d in DAYS}
    c = {d: rng.randint(0, 20) for d in DAYS}
    assert sd.lead_lag(s, c, DAYS)["reading"] in ("apart", "flat")
    assert sd.lead_lag(s, c, DAYS[:5])["reading"] == "too_few_days"


def test_coverage_nobody_went_looking_for_is_counted():
    search = {d: 10.0 for d in DAYS}
    coverage = {d: 2 for d in DAYS}
    coverage[DAYS[5]] = 40
    coverage[DAYS[15]] = 35
    search[DAYS[15]] = 90.0
    tr = sd.traction(search, coverage, DAYS)
    by_day = {p["date"]: p["moved"] for p in tr["coverage_peaks"]}
    assert by_day == {DAYS[5]: False, DAYS[15]: True}


def test_the_public_searching_while_the_press_was_quiet_is_counted():
    search = {d: 10.0 for d in DAYS}
    coverage = {d: 5 for d in DAYS}
    search[DAYS[12]] = 95.0
    coverage[DAYS[12]] = 3
    quiet = sd.traction(search, coverage, DAYS)["search_without_coverage"]
    assert [q["date"] for q in quiet] == [DAYS[12]]


FINDING = {"statement": "Jane Wanjiru was summoned by the Senate Finance Committee over county disbursements."}
STORY = {"label": "Treasury standoff", "description": "The Treasury and delayed county disbursements."}


@pytest.mark.parametrize("query,state", [
    ("jane wanjiru news", "navigational"),
    ("wanjiru senate committee", "on_record"),
    ("wanjiru treasury", "on_record"),
    ("wanjiru maternity wing", "in_coverage"),
    ("wanjiru husband", "unanswered"),
])
def test_each_search_is_held_against_what_the_report_holds(query, state):
    got = sd.answered(query, {"jane", "wanjiru"}, [FINDING], [STORY],
                      ["Jane Wanjiru opened a maternity wing in Kiambu."])
    assert got["state"] == state


def _measured(location, queries_top, queries_rising, values=None):
    explore = {"series": [_series("Jane Wanjiru", values or [20] * 31)],
               "averages": [{"keyword": "Jane Wanjiru", "value": 20}]}
    return {"location": location, "timeframe": "past_30_days", "timeframe_label": "the past 30 days",
            "keywords": ["Jane Wanjiru"], "keyword": "Jane Wanjiru",
            "explore": {"state": "measured", "data": explore, "provider": "SocialCrawl (Google Trends)"},
            "rising": {"state": "measured", "provider": "SocialCrawl (Google Trends)",
                       "data": {"top": [{"query": q, "value": 50} for q in queries_top],
                                "rising": [{"query": q, "value": 300} for q in queries_rising]}}}


def _gathered():
    search, coverage = _lead_series()
    return {"KE": _measured("KE", ["jane wanjiru news", "wanjiru treasury"], ["wanjiru husband"], search),
            "": _measured("", ["wanjiru treasury"], ["wanjiru imf loan"]),
            "suggestions": {"KE": {"state": "measured", "suggestions": [{"query": "jane wanjiru age", "prompt": "x"}]},
                            "": {"state": "not_measured", "reason": "HTTP 403", "suggestions": []}},
            "wikipedia": {"state": "measured", "title": "Jane Wanjiru", "url": "https://en.wikipedia.org/wiki/Jane_Wanjiru",
                          "series": [{"date": DAYS[3], "views": 200}, {"date": DAYS[4], "views": 900}], "total": 1100}}


def _section():
    _search, coverage = _lead_series()
    return sd.assemble(_gathered(), START, END, "Jane Wanjiru", dict(zip(DAYS, coverage)),
                       [FINDING], [STORY], ["Jane Wanjiru opened a maternity wing."])


def test_the_section_reads_both_places_and_what_separates_them():
    section = _section()
    kenya, world = section["kenya"], section["world"]
    assert kenya["state"] == world["state"] == "measured"
    assert kenya["lead_lag"]["reading"] == "search_led"
    assert "lead_lag" not in world, "the world's curve is not timed against Kenyan coverage"
    assert kenya["unanswered"] == ["wanjiru husband"]
    assert section["contrast"]["world_only"] == ["wanjiru imf loan"]
    assert kenya["storyline_searches"]["Treasury standoff"][0]["query"] == "wanjiru treasury"
    assert "not a number of searches" in kenya["scale_note"]
    assert kenya["suggested"]["unanswered"] == ["jane wanjiru age"]
    assert world["suggested"]["state"] == "not_measured", "a refused host is not an empty list"
    assert section["wikipedia"]["peak"]["views"] == 900


def test_nothing_measured_anywhere_is_not_measured_rather_than_quiet():
    down = {"state": "not_measured", "reason": "down", "data": None}
    gathered = {loc: {"location": loc, "explore": down, "rising": down} for loc in ("KE", "")}
    gathered["suggestions"] = {loc: {"state": "not_measured", "reason": "HTTP 403"} for loc in ("KE", "")}
    gathered["wikipedia"] = {"state": "not_measured", "reason": "no article"}
    section = sd.assemble(gathered, START, END, "Jane Wanjiru")
    assert section["state"] == "not_measured"
    assert section["kenya"]["reason"] == "down"


def test_since_the_last_report_names_only_new_searches():
    previous = _section()
    gathered = _gathered()
    gathered["KE"]["rising"]["data"]["rising"].append({"query": "wanjiru running mate", "value": 120})
    section = sd.assemble(gathered, START, END, "Jane Wanjiru", previous=previous)
    assert section["kenya"]["new_since_last"] == ["wanjiru running mate"]


# --- for the writers -------------------------------------------------------------

def test_every_signal_is_counted_and_links_to_where_it_can_be_checked():
    signals = sd.all_signals(_section(), "Jane Wanjiru")
    sentences = [s["sentence"] for s in signals]
    assert any("rose 300%" in t and "in Kenya" in t for t in sentences)
    assert any("tended to rise 1 day(s) before coverage" in t for t in sentences)
    assert any("worldwide" in t and "but not among those in Kenya" in t for t in sentences)
    assert any("Wikipedia" in t and "1,100" in t for t in sentences)
    assert all(s["url"] for s in signals)


def _ledger():
    findings = [FINDING | {"status": "reported", "independent_sources": 2,
                           "sources": [{"publisher": "Daily Nation", "url": "https://n.example", "ref": "aaaa1111",
                                        "quote": "summoned by the Senate Finance Committee"}]}]
    return from_facts.build_ledger(findings) + from_facts.search_entries(
        sd.all_signals(_section(), "Jane Wanjiru"))


def test_a_search_may_not_be_written_as_what_happened():
    ledger = _ledger()
    rising = next(e for e in ledger if e.get("search") and "rose 300%" in e["statement"])
    assert from_facts.check_sentence("Searches in Kenya for “wanjiru husband” rose 300% over the past 30 days.",
                                     [rising["id"]], ledger, "Jane Wanjiru") is None
    reason = from_facts.check_sentence("Wanjiru's husband rose 300% in Kenya over the past 30 days.",
                                       [rising["id"]], ledger, "Jane Wanjiru")
    assert reason == "states what people looked up as though it were what happened"


def test_search_entries_are_labelled_for_the_writer_and_their_strength_is_their_own():
    ledger = _ledger()
    text = from_facts.ledger_text(ledger)
    assert "[SEARCH · what people looked up — attention, not fact]" in text
    s_id = next(e["id"] for e in ledger if e.get("search"))
    assert from_facts.strength([s_id], ledger) == "searched"
    assert from_facts.strength([s_id, "F1"], ledger) == "corroborated", "the findings still decide"


def test_the_writer_is_handed_the_search_entries(monkeypatch):
    from engine import llm

    seen = []
    monkeypatch.setattr(llm, "call_json_untrusted",
                        lambda instructions, untrusted, expected_keys, **kw: seen.append(untrusted) or
                        {"insights": [], "the_one_thing": {}})
    record = {"findings": [FINDING | {"status": "reported", "independent_sources": 2, "sources": []}] * 3}
    from_facts.insights_from_record("Jane Wanjiru", record,
                                    search=sd.all_signals(_section(), "Jane Wanjiru"))
    assert "S1 [SEARCH" in seen[0]


# --- the run and the page ----------------------------------------------------------

def test_the_run_reads_search_demand_beside_the_report(db_session, scripted_model, monkeypatch):
    from engine.config import settings
    from engine.tests.test_end_to_end_run import _api_shape, _run, _seed

    monkeypatch.setattr(settings, "enable_search_demand", True)
    monkeypatch.setattr(sd, "gather", lambda *a, **k: _gathered())
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    section = payload["search_demand"]
    assert section["kenya"]["state"] == "measured"
    assert section["kenya"]["coverage_by_day"], "the chart draws the coverage the reading used"
    assert _api_shape(payload, db_session, subject.id)["searchDemand"]["state"] == "measured"


def test_a_search_demand_that_never_answers_costs_the_section_not_the_report(db_session, scripted_model,
                                                                            monkeypatch):
    import time

    from engine import stages
    from engine.config import settings
    from engine.tests.test_end_to_end_run import _run, _seed

    monkeypatch.setattr(settings, "enable_search_demand", True)
    monkeypatch.setattr(settings, "search_demand_deadline_seconds", 1.0)
    monkeypatch.setattr(sd, "gather", lambda *a, **k: time.sleep(8) or _gathered())
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    assert "search_demand" not in payload
    assert payload.get("executive_summary"), "the report was delivered"


def test_the_page_says_what_the_index_is_and_keeps_failures_for_the_operator():
    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    at = html.index("function renderSearchDemand(")
    body = html[at:at + 4000]
    assert "c.classList.add('op-only')" in body, "an unmeasured section is the operator's, not an empty card"
    assert "never compared as amounts" in body
    assert "renderSearchDemand(r.searchDemand)" in html
    assert "['search_demand','What people searched for']" in html
