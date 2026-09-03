"""Sections of the client's framework that were structurally guaranteed to be
blank, whatever the data.

Four of them, and none was a thin-corpus problem — each was empty by
construction on every issue map ever produced:

  - `background.international` / `background.national` read
    `international_context` / `national_context` from the payload, and nothing
    anywhere ever wrote those keys.
  - `stakeholder_networks` was built with `relationships=[]` passed in
    literally, so the network section rendered without a single edge.
  - the hover profile's `history`, `track_record` and `modus_operandi` were
    read and never written — three empty fields under every name.
  - `sequencing` kept only developments dated in the FUTURE. An issue map runs
    over a look-back window, so everything it finds has already happened.
"""

from datetime import datetime

from engine.reports import analysts, issue_framework, issue_map

ANALYSIS = {
    "involvement": "He challenged the borrowing in court.",
    "verdict": "The principal challenger.",
    "international": "The Fund's programme history across the region.",
    "national": "Kenya's borrowing record since 2013.",
    "key_actors": [
        {"name": "Okiya Omtatah", "entity_type": "person", "position": "against",
         "influence": 85, "relation": "Filed the petition.",
         "track_record": "A serial public-interest litigant.",
         "modus_operandi": "Constitutional petitions."},
        {"name": "National Treasury", "entity_type": "organization", "position": "for",
         "influence": 75, "relation": "Defends the borrowing."},
        {"name": "Katiba Institute", "entity_type": "organization", "position": "against",
         "influence": 40, "relation": "Amicus."},
    ],
    "timeline": [
        {"date": "2026-06-01", "event": "Okiya Omtatah files against National Treasury",
         "sources": 3},
    ],
    "sub_issues": [{"sub_issue": "Disclosure", "root": True,
                    "actors": ["National Treasury", "Katiba Institute"]}],
}


def _framework():
    return issue_map._issue_framework("Okiya Omtatah", "IMF", {"coverage": {}}, ANALYSIS)


def test_the_background_has_both_of_its_halves():
    background = _framework()["background_and_context"]
    assert background["international"].strip()
    assert background["national"].strip()


def test_an_analyst_is_actually_asked_for_that_background():
    """The keys can only be filled if something writes them."""
    assert "background" in analysts.ISSUE_SECTIONS
    _prompt, keys = analysts.ISSUE_SECTIONS["background"]
    assert set(keys) == {"international", "national"}


def test_the_stakeholder_network_has_edges():
    networks = _framework()["stakeholder_networks"]
    assert networks["visualisation"]["edges"], "a network section with no links is a list"
    linked = [p for p in networks["challengers"] + networks["champions"] if p["network"]]
    assert linked, "no stakeholder is connected to any other"


def test_relationships_come_from_the_record_not_from_nothing():
    known = {"Okiya Omtatah", "National Treasury", "Katiba Institute"}
    edges = issue_map._actor_relationships(ANALYSIS, known)
    kinds = {e["rel_type"] for e in edges}
    assert any("same development" in k for k in kinds)
    assert any("both on" in k for k in kinds)
    assert any("same stance" in k for k in kinds)
    # Both directions, so either name finds the other.
    pairs = {(e["source"], e["target"]) for e in edges}
    assert ("Okiya Omtatah", "National Treasury") in pairs
    assert ("National Treasury", "Okiya Omtatah") in pairs


def test_an_actor_nobody_reported_is_never_linked():
    edges = issue_map._actor_relationships(
        {"sub_issues": [{"sub_issue": "x", "actors": ["Ghost"]}]}, {"Okiya Omtatah"})
    assert edges == []


def test_the_hover_profile_is_not_three_empty_fields():
    profile = next(p for p in _framework()["stakeholder_networks"]["challengers"]
                   if p["name"] == "Okiya Omtatah")["profile"]
    assert profile["track_record"].strip()
    assert profile["modus_operandi"].strip()
    assert profile["history"].strip()


def test_the_actors_analyst_asks_for_the_profile_fields():
    prompt = analysts.ISSUE_ACTORS_PROMPT
    assert "track_record" in prompt and "modus_operandi" in prompt


def test_sequencing_is_not_empty_just_because_the_window_looks_backward():
    background = {"timeline_of_major_developments": [
        {"date": "2026-06-01", "event": "A"}, {"date": "2026-07-02", "event": "B"}]}
    out = issue_framework.build_sequencing(background, now=datetime(2026, 9, 3))
    assert out["looking"] == "backward"
    assert out["engagement_timeline"], "the whole section rendered empty on every map"
    assert out["coalition_windows"]
    assert "not dated ahead of today" in out["note"] or "reached" in out["note"]


def test_sequencing_still_looks_forward_when_there_is_something_ahead():
    background = {"timeline_of_major_developments": [
        {"date": "2026-06-01", "event": "past"}, {"date": "2027-01-01", "event": "ahead"}]}
    out = issue_framework.build_sequencing(background, now=datetime(2026, 9, 3))
    assert out["looking"] == "forward"
    assert [i["event"] for i in out["engagement_timeline"]] == ["ahead"]


def test_no_dates_at_all_says_so_rather_than_showing_nothing():
    out = issue_framework.build_sequencing({"timeline_of_major_developments": []})
    assert out["engagement_timeline"] == []
    assert "no sequence can be built" in out["note"].lower()
