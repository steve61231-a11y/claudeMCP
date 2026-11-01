"""Scenario mapping: the model's judgement, held to the record and recomputed.

The client's model of this section is a 2022 election paper: risk drivers
with how each reads now, a branching tree with a probability on every
branch, and three scenarios — most likely, credible alternative, outlier.
A model asked for that writes it fluently from nothing. So every reading
and factor cites findings and may not introduce a name, figure or date they
lack; the branches must add up; each scenario's probability is computed
from its path, never taken from the writer; and the tiers are the order of
those computed figures. The paper itself headlined "45%" over a branch that
read 40% — a figure in a client's file must be one the page can recompute.
"""

from pathlib import Path

import pytest

from engine.reports import scenarios


def _finding(statement, status="reported", origins=1):
    return {"statement": statement, "status": status, "independent_sources": origins,
            "first_seen": "2026-10-02",
            "sources": [{"publisher": "Daily Nation", "ref": "abcd1234", "url": "https://n.example/a",
                         "quote": statement.lower()}]}


LINKED = [
    _finding("Okiya Omtatah petitioned the High Court over the IMF loan.", origins=3),
    _finding("Okiya Omtatah alleged the IMF loan was signed without Parliament.", status="alleged"),
]
OTHERS = [_finding("Okiya Omtatah toured Busia county.")]
RECORD = {"state": "measured", "findings": LINKED, "patterns": []}

TREE = [
    {"id": "n1", "label": "High Court hearing", "kind": "event"},
    {"id": "n2", "parent": "n1", "label": "Court admits the petition", "p": 60},
    {"id": "n3", "parent": "n1", "label": "Court dismisses the petition", "p": 40},
    {"id": "n4", "parent": "n2", "label": "Loan conditions suspended", "p": 30},
    {"id": "n5", "parent": "n2", "label": "Loan proceeds under review", "p": 70},
]


def test_a_path_is_the_product_of_its_branches():
    tree, problem = scenarios.validate_tree(TREE)
    assert problem is None
    by = {n["id"]: n for n in tree}
    assert by["n4"]["path_p"] == 18.0 and by["n5"]["path_p"] == 42.0 and by["n3"]["path_p"] == 40.0
    assert scenarios.scenario_probability(["n4", "n3"], tree) == (58.0, None)


@pytest.mark.parametrize("change,needle", [
    ({"id": "n3", "p": 30}, "add up to 90%"),
    ({"id": "n2", "parent": None}, "2 starting points"),
    ({"id": "n4", "parent": "n9"}, "does not exist"),
])
def test_a_tree_that_does_not_add_up_is_withdrawn_with_the_reason(change, needle):
    nodes = [dict(n) for n in TREE]
    for n in nodes:
        if n["id"] == change["id"]:
            n.update(change)
    tree, problem = scenarios.validate_tree(nodes)
    assert tree == [] and needle in problem


def test_a_single_unchosen_step_is_an_intervening_step_at_one_hundred():
    tree, problem = scenarios.validate_tree([{"id": "a", "label": "Second round"},
                                             {"id": "b", "parent": "a", "label": "Reassess"}])
    assert problem is None and tree[1]["p"] == 100 and tree[1]["kind"] == "intervening"


def test_a_scenario_may_not_count_a_path_twice():
    tree, _ = scenarios.validate_tree(TREE)
    assert "its own ancestors" in scenarios.scenario_probability(["n2", "n4"], tree)[1]


def _reply(**over):
    reply = {
        "drivers": [
            {"category": "legal", "driver": "Litigation over the loan",
             "definition": "Court petitions can suspend a loan's conditions.",
             "impact": "A petition admitted raises the cost of proceeding.",
             "reading": "Omtatah has petitioned the High Court over the IMF loan.",
             "direction": "raises", "facts": ["F1"]},
            {"category": "Political", "driver": "Parliamentary oversight",
             "reading": "Treasury Cabinet Secretary John Mbadi signed it in 2025.",
             "direction": "raises", "facts": ["F2"]},
            {"category": "Political", "driver": "Unsupported", "reading": "Something.", "facts": []},
        ],
        "tree": TREE,
        "scenarios": [
            {"title": "The loan proceeds under review", "nodes": ["n5"],
             "factors": [{"text": "The petition is before the High Court.", "facts": ["F1"]}],
             "outlook": [{"text": "The court hears the petition."}],
             "implications": [{"text": "Raila Odinga gains leverage."}]},
            {"title": "The petition fails", "nodes": ["n3"],
             "factors": [{"text": "Omtatah alleged the loan bypassed Parliament.", "facts": ["F2"]}]},
            {"title": "Conditions suspended", "nodes": ["n4"],
             "factors": [{"text": "It costs 500 billion shillings.", "facts": ["F1"]}]},
        ],
    }
    reply.update(over)
    return reply


def test_the_section_is_held_to_the_record_and_tiered_by_computed_figures(monkeypatch):
    monkeypatch.setattr(scenarios, "write", lambda *a, **k: _reply())
    out = scenarios.build("Okiya Omtatah", "IMF", RECORD, LINKED + OTHERS)
    assert out["state"] == "modelled"
    assert [d["driver"] for d in out["drivers"]] == ["Litigation over the loan"]
    assert out["drivers"][0]["category"] == "Legal" and out["drivers"][0]["reading_citations"]
    reasons = " | ".join(r["reason"] for r in out["check"]["removed"])
    assert "introduces a date (2025)" in reasons and "no reading on the record" in reasons
    # The third scenario's only factor invented a figure, so it falls.
    assert [(s["tier"], s["probability"]) for s in out["scenarios"]] == \
        [("most_likely", 42.0), ("credible_alternative", 40.0)]
    first = out["scenarios"][0]
    assert first["outlook"] and not first["implications"], "an outlook naming no one stands; Raila Odinga is not on the record"
    assert out["scenarios"][1]["strength"] == "alleged"
    assert {n["id"]: n.get("tier") for n in out["tree"]}["n5"] == "most_likely"


def test_a_thin_record_is_not_modelled_and_says_why(monkeypatch):
    monkeypatch.setattr(scenarios, "write", lambda *a, **k: pytest.fail("no model call on a thin record"))
    none_linked = scenarios.build("Okiya Omtatah", "IMF", {"state": "measured", "findings": []}, OTHERS)
    assert none_linked["state"] == "not_modelled" and "names both" in none_linked["reason"]
    thin = scenarios.build("Okiya Omtatah", "IMF", RECORD, LINKED)
    assert "fewer than 3 findings" in thin["reason"]
    unread = scenarios.build("Okiya Omtatah", "IMF", {"state": "not_measured"}, None)
    assert unread["state"] == "not_modelled"


def test_a_tree_label_naming_someone_off_the_record_withdraws_the_tree(monkeypatch):
    bad = [dict(n) for n in TREE]
    bad[1]["label"] = "Justice Martha Koome admits it"
    monkeypatch.setattr(scenarios, "write", lambda *a, **k: _reply(tree=bad))
    out = scenarios.build("Okiya Omtatah", "IMF", RECORD, LINKED + OTHERS)
    assert out["tree"] == [] and out["scenarios"] == []
    assert "Martha Koome" in out["tree_withheld"]
    assert out["drivers"], "the drivers stand without the tree"


def test_the_page_draws_it_in_the_framework_and_keeps_the_failures_for_the_operator():
    """The founder put scenarios in Strategic recommendations: "we need to
    show our workings with a probability tree". So the baseline bars are the
    tree summed, and the tree is drawn beneath them."""
    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    parts = html[html.index("function scenarioParts("):]
    assert "scenarioTree(sc.tree)" in parts and "linkifyRefs(esc(d.reading" in parts
    assert "f.classList.add('op-only')" in parts[:6000]
    fw = html[html.index("function renderIssueFramework("):]
    s4 = fw[fw.index("// 4 Strategic recommendations"):fw.index("// 5 Sequencing")]
    assert "probabilityBars({baseline:sc.baseline})" in s4 and "Our workings: the probability tree" in s4
    assert "Major variables at play" in s4 and "Recommendations" in s4
    src = (Path(__file__).resolve().parents[1] / "reports" / "issue_map.py").read_text()
    assert 'payload["connection_record"].pop("_principal_findings", None)' in src, \
        "the principal's whole record never reaches the stored payload"
    assert 'protected_terms.redact(payload["scenarios"], mentions)' in src, \
        "the identity rule covers the scenarios as it covers the analysts"


def test_the_baseline_is_the_tree_summed_so_the_workings_show():
    """The founder: "I like the baseline prob section, we need to show our
    workings with a probability tree." So the bars ARE the tree: the end
    nodes where the desired outcome prevails, summed."""
    tagged = [dict(n) for n in TREE]
    outcome = {"n3": "opposed", "n4": "desired", "n5": "undetermined"}
    for n in tagged:
        if n["id"] in outcome:
            n["outcome"] = outcome[n["id"]]
    tree, _ = scenarios.validate_tree(tagged)
    base, problem = scenarios.baseline(tree)
    assert problem is None
    assert (base["desired_outcome_prevails"], base["opposed_outcome_prevails"], base["undetermined"]) == (18.0, 40.0, 42.0)
    assert [w["path"] for w in base["workings"]][0] == "Loan proceeds under review"
    untagged, _ = scenarios.validate_tree(TREE)
    assert scenarios.baseline(untagged)[0] is None, "an end node that does not say which way it falls is no baseline"


def test_advice_names_only_who_the_record_names_and_cites_it():
    recs = [
        {"action": "Brief the petitioner's counsel before the hearing", "who": "Okiya Omtatah",
         "when": "before the High Court hearing", "rationale": "Omtatah petitioned the High Court over the IMF loan.",
         "facts": ["F1"]},
        {"action": "Lobby Senator Edwin Sifuna", "who": "Edwin Sifuna", "facts": ["F1"]},
        {"action": "Issue a statement", "rationale": "It helps."},
    ]
    ledger = scenarios.ledger_for(LINKED, OTHERS)
    kept, removed = scenarios.check_recommendations(recs, ledger, "Okiya Omtatah")
    assert [r["action"] for r in kept] == ["Brief the petitioner's counsel before the hearing"]
    assert kept[0]["rationale_citations"]
    reasons = " | ".join(r["reason"] for r in removed)
    assert "Edwin Sifuna" in reasons and "cites nothing" in reasons
