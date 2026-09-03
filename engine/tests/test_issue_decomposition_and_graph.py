"""The two pieces Issue Mapping was missing: reading the question, and
connecting what came back.

Both used to be absent in a way that looked like an empty result. A query box
holding a whole claim ("Odious debt case by Okiya Omtatah") was matched as one
literal phrase, so the single most on-topic article in the corpus was thrown
away and the map reported that nothing existed. And the map that did render
was a picture, not a structure — nothing in it was addressable.
"""
import re

from engine.reports import decompose, issue_graph, relevance


def test_a_claim_with_a_name_in_it_yields_the_name():
    parts = decompose.decompose("Odious debt case by Okiya Omtatah")
    assert "Okiya Omtatah" in parts["names"]
    # The raw phrase stays available; it is an identity, not the only one.
    assert parts["raw"] in parts["identities"]
    assert len(parts["identities"]) > 1


def test_a_bare_name_survives_decomposition_unharmed():
    parts = decompose.decompose("Uhuru Kenyatta")
    assert "Uhuru Kenyatta" in parts["identities"]


def test_acronyms_are_kept_as_their_own_identity():
    parts = decompose.decompose("International Monetary Fund (IMF)")
    assert "IMF" in parts["acronyms"] or "IMF" in parts["identities"]


def test_research_dimensions_are_more_than_the_pair_as_asked():
    dims = decompose.research_dimensions("Okiya Omtatah", "odious debt")
    names = {d["dimension"] for d in dims}
    assert {"intersection", "conflict", "history"} <= names
    for d in dims:
        assert d["queries"], f"{d['dimension']} has no queries"
        assert d["why"], f"{d['dimension']} does not say why it is asked"


def test_merge_terms_keeps_both_sides_without_duplicates():
    merged = relevance.merge_terms(["Okiya Omtatah"], ["okiya omtatah", "Odious"])
    lowered = [t.lower() for t in merged]
    assert len(lowered) == len(set(lowered))
    assert "odious" in lowered


ANALYSIS = {
    "key_actors": [
        {"name": "Okiya Omtatah", "entity_type": "person", "position": "against",
         "influence": 80, "relation": "Filed the petition.",
         "quotes": [{"text": "Omtatah filed the case", "url": "https://n/1"}]},
        {"name": "National Treasury", "entity_type": "organization", "position": "for",
         "influence": 70, "relation": "Defends the borrowing.",
         "quotes": [{"text": "Treasury defended", "url": "https://n/2"}]},
    ],
    "linking_narratives": [
        {"narrative": "Odious debt doctrine", "strength": 60, "summary": "s",
         "quotes": [{"text": "doctrine cited", "url": "https://n/3"}]},
    ],
    "timeline": [
        {"date": "2026-06-01", "event": "Okiya Omtatah files petition at the High Court",
         "sources": 3, "quotes": [{"text": "filed", "url": "https://n/4"}]},
    ],
    "sub_issues": [
        {"sub_issue": "Whether the loan agreements can be withheld from Parliament",
         "question": "Are the agreements public documents?", "root": True,
         "actors": ["National Treasury"], "detail": "d",
         "quotes": [{"text": "withheld", "url": "https://n/6"}]},
    ],
    "involvement": "The senator is challenging the debt.",
    "verdict": "contested",
}


def _graph():
    return issue_graph.build(
        "Okiya Omtatah", "odious debt", ANALYSIS, None,
        corpus=[{"platform": "nation.africa", "text": "t", "source_url": "https://n/5"}])


def test_the_graph_is_connected_not_a_spiderweb():
    g = _graph()
    assert g["stats"]["nodes"] >= 5
    assert g["stats"]["edges"] >= 4
    # Every node must be reachable; a floating dot is decoration.
    assert g["stats"]["isolated"] == 0


def test_every_edge_points_at_nodes_that_exist():
    g = _graph()
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids


def test_an_edge_without_evidence_is_not_drawn():
    """The rule the whole map rests on: no evidence, no claim."""
    g = _graph()
    for e in g["edges"]:
        if e["confidence"] != issue_graph.CONFIDENCE_INFERRED:
            assert e.get("evidence"), f"{e['relation']} asserts a link with nothing behind it"


def test_the_principal_is_not_duplicated_as_an_actor():
    g = _graph()
    labels = [n["label"].lower() for n in g["nodes"]]
    assert labels.count("okiya omtatah") == 1


def test_an_event_links_to_the_people_it_names():
    g = _graph()
    events = [n for n in g["nodes"] if n["type"] == "event"]
    assert events
    ev = events[0]["id"]
    touching = [e for e in g["edges"] if ev in (e["source"], e["target"])]
    assert len(touching) >= 2, "an event connected to nothing is a date, not a finding"


def test_hidden_views_stay_hidden():
    """`#zenith .grid{display:grid}` outranked the browser's [hidden] rule, so
    switching tabs stacked the views instead of replacing them."""
    html = open("web/pulse_app.html", encoding="utf-8").read()
    assert re.search(r"#zenith \[hidden\]\s*\{\s*display:\s*none\s*!important", html)


def test_the_graph_is_published_before_the_slowest_call(monkeypatch):
    """The framework is the longest call in the run. Building the map after it
    meant the reader watched a stage line for minutes while the graph — pure
    local work over data already in hand — sat unbuilt."""
    from engine.reports import issue_map

    order = []
    monkeypatch.setattr(issue_map, "_issue_framework",
                        lambda *a, **k: order.append("framework") or {})
    payload = {}
    issue_map._publish_graph("P", "I", ANALYSIS, None, [], payload,
                             lambda k, v: order.append(k))
    issue_map._issue_framework()
    assert order == ["issue_graph", "framework"]
    assert payload["issue_graph"]["stats"]["nodes"]


def test_sub_issues_hang_off_the_issue_and_name_their_actors():
    """An issue map that cannot say what the issue breaks into is a map of one
    thing. The sub-issue's actors are joined to it only when those actors were
    actually reported — naming someone does not conjure them into the graph."""
    g = _graph()
    subs = [n for n in g["nodes"] if n["type"] == "sub_issue"]
    assert len(subs) == 1
    assert subs[0]["root"] is True
    sid = subs[0]["id"]
    relations = {e["relation"] for e in g["edges"] if sid in (e["source"], e["target"])}
    assert "root of" in relations
    assert "is on" in relations


def test_a_sub_issue_naming_an_unknown_actor_invents_nothing():
    analysis = dict(ANALYSIS)
    analysis["sub_issues"] = [{"sub_issue": "x", "actors": ["Nobody At All"],
                               "quotes": [{"text": "q", "url": "https://n/7"}]}]
    g = issue_graph.build("Okiya Omtatah", "odious debt", analysis, None, corpus=[])
    assert not [n for n in g["nodes"] if n["label"] == "Nobody At All"]
