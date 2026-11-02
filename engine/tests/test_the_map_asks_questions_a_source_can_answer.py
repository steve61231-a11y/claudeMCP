"""The map asks every source a question that source can answer.

A live map — Kenya Government × "Kenyan Court stopping the Kenyan
government's sale of its Safaricom shares to Vodacom", 90 days — read 16
documents and kept 6, every one from YouTube, on one of the year's largest
business stories. The issue had gone to GDELT, Google News, Reddit and
YouTube as one quoted sentence that no article prints; decomposition picked
"Kenyan Court" as the issue's name and let the word "Kenyan" alone count as
the issue; the history dimension asked for 2019-2021 whatever the window;
and "Kenya Government" was searched as "Hon Government".
"""

from datetime import datetime

from engine.reports import decompose, issue_map

SENTENCE = "Kenyan Court stopping the Kenyan government's sale of its Safaricom shares to Vodacom"


def test_a_sentence_is_never_searched_as_typed():
    terms = issue_map.issue_search_terms(SENTENCE)
    assert SENTENCE not in terms
    assert terms[:4] == ["Safaricom shares", "Safaricom sale", "Safaricom", "Vodacom"]


def test_a_short_issue_is_still_searched_as_typed_first():
    assert issue_map.issue_search_terms("Finance Bill 2024")[0] == "Finance Bill 2024"
    assert issue_map.issue_search_terms("SHA", ["Social Health Authority"]) == ["SHA", "Social Health Authority"]
    assert issue_map.issue_search_terms("SHA") == ["SHA", "Social Health Authority"], "expanded without being told"
    assert issue_map.issue_search_terms("Adani deal at JKIA")[:3] == ["Adani deal at JKIA", "Adani", "JKIA"]
    assert "Jomo Kenyatta International Airport" in issue_map.issue_search_terms("Adani deal at JKIA")


def test_no_demonym_or_descriptive_name_stands_for_the_issue_beside_a_real_one():
    parts = decompose.decompose(SENTENCE)
    assert parts["anchors"] == ["Safaricom", "Vodacom"]
    lowered = [i.lower() for i in parts["identities"]]
    assert "kenyan" not in lowered and "kenyan court" not in lowered
    assert decompose.decompose("National Treasury")["anchors"] == ["National Treasury"], \
        "with nothing more distinctive, the descriptive name is what there is"


def test_a_pairing_word_is_a_noun_beside_the_name_never_a_verb():
    built = decompose.phrases(SENTENCE, ["Safaricom", "Vodacom"])
    assert built == ["Safaricom shares", "Safaricom sale"]
    assert not any("stopping" in p for p in built)


def test_history_is_dated_from_the_window():
    dims = decompose.research_dimensions("Kenya Government", SENTENCE, window_start=datetime(2026, 8, 1))
    history = next(d for d in dims if d["dimension"] == "history")["queries"]
    assert any("2025" in q for q in history) and not any("2019" in q for q in history)
    assert all('"Safaricom"' in q for d in dims if d["dimension"] in ("conflict", "history") for q in d["queries"])


def test_an_institution_is_searched_as_the_press_prints_it_never_as_a_person():
    assert issue_map.subject_type_for("Kenya Government") == "organization"
    assert issue_map.subject_type_for("Okiya Omtatah") == "politician"
    assert issue_map.principal_variants("Kenya Government") == [
        "Kenya Government", "Kenyan Government", "Government of Kenya"]

    class Old:  # a row an earlier map stored as a person
        name, aliases, titles, swahili_terms, subject_type = "Kenya Government", ["Kenya Government"], [], [], "politician"
    ids = issue_map.map_identities(Old(), "Kenya Government")
    assert not any(v.lower().startswith("hon ") for v in ids)
    assert "Government of Kenya" in ids


def test_the_news_sources_also_ask_for_the_issue_alone(monkeypatch):
    asked = []
    for name in ("_gdelt_intersection", "_google_news_intersection", "_reddit_intersection", "_youtube_intersection"):
        monkeypatch.setattr(issue_map, name, lambda i, t, ws, we, n=name: asked.append((n, i, t)) or [])
    monkeypatch.setattr(issue_map, "_social_tier", lambda: ("free", None, "no key"))
    issue_map.acquire_intersection("Kenya Government", SENTENCE, datetime(2026, 8, 1), datetime(2026, 10, 30),
                                   identities=issue_map.principal_variants("Kenya Government"),
                                   issue_terms=issue_map.issue_search_terms(SENTENCE))
    assert all(t != SENTENCE for _, _, t in asked)
    alone = [(n, t) for n, i, t in asked if not i]
    assert ("_google_news_intersection", "Safaricom shares") in alone
    assert not any(n in ("_reddit_intersection", "_youtube_intersection") for n, _ in alone)
    assert issue_map._both_terms_for_quoting_source("", "Safaricom shares") == "Safaricom shares"


def test_gdelt_keeps_the_date_it_was_published(monkeypatch):
    """The map's GDELT path clamped an older article to the window's first
    day and stamped an undated one with its last — the defect the Search
    report fixed with `period_date`, never fixed here."""
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"articles": [
                {"url": "https://n.example/old", "title": "Old", "seendate": "20250101T000000Z", "domain": "n.example"},
                {"url": "https://n.example/new", "title": "New", "seendate": "20261001T120000Z", "domain": "n.example"},
                {"url": "https://n.example/undated", "title": "Undated", "domain": "n.example"}]}

    monkeypatch.setattr(issue_map.http, "get", lambda *a, **k: Resp())
    out = issue_map._gdelt_intersection("Kenya Government", "Safaricom", datetime(2026, 8, 1), datetime(2026, 10, 30))
    by_title = {m["text"]: m for m in out}
    assert "Old" not in by_title, "published before the window: not this map's evidence"
    assert by_title["New"]["posted_at"] == datetime(2026, 10, 1, 12, 0)
    assert by_title["Undated"]["raw_payload"]["date_estimated"] is True
    corpus = issue_map._as_corpus(out)
    assert {c["text"]: c["date_estimated"] for c in corpus} == {"New": False, "Undated": True}
