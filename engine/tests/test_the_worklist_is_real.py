"""The section that tells a client what to do had never had any data.

The dashboard has rendered "Actions in Progress" since it was built. It
read `payload["actions"]`, then `payload["recommendations"]`, then
`sentiment_framework.strategic_implications.recommended_actions` — and
nothing in this engine has ever written any of the three. So on every live
report it printed "no actions recorded for this period", which reads as a
quiet week and was in fact a section nobody had finished. In the part of
the report whose entire job is to tell someone to act.

It also invented a number. The progress bar drew 50% for anything whose
status was "in_progress" and 15% for everything else: a measurement with no
referent, rendered as a measurement, in a product whose whole argument is
that its figures can be checked.

What is pinned here:

- the worklist is built from the analysts' own judgements, not from nothing;
- every action is tied to the narrative it addresses, or says it is not,
  because the ordering depends on that and a guessed match would put a
  made-up size next to an action;
- "in progress" means it was on the previous report's list, and the number
  of reports is counted rather than asserted;
- movement is two stored numbers subtracted, and where it cannot be
  computed the reason is given instead of a zero.
"""

import re

import pytest

from engine.reports import actions, prose

NARRATIVES = [
    {"label": "SHA rollout pain", "mentions": 214,
     "description": "Facilities allegedly still charging; delays in registration at the point of care."},
    {"label": "Taxation & cost of living", "mentions": 151,
     "description": "New levies framed against household budgets."},
    {"label": "Reform defense", "mentions": 98,
     "description": "Official framing of long-term gains."},
]

RISK_POC = ("A growing negative narrative around point-of-care charges, reported "
            "across counties, is unanswered by any named directive. Registered "
            "members describe being asked for cash at facilities.")
RISK_LEVY = ("The levy is read as a tax because deduction is visible monthly and "
             "the benefit is not. That attaches health financing to the budget "
             "argument. Cost-of-living framing dominates the replies.")
OPP_MIDDLE = ("The ambivalent middle separates the design of the scheme from its "
              "rollout. A third of scored mentions withhold judgement. Reform "
              "defense messaging reaches them.")

CURRENT = {"narratives": NARRATIVES,
           "risks": [RISK_POC, RISK_LEVY],
           "opportunities": [OPP_MIDDLE]}


def test_a_worklist_is_produced_from_what_the_analysts_already_wrote():
    out = actions.build(CURRENT)
    assert len(out["items"]) == 3, out
    assert all(item["action"] for item in out["items"])
    assert all(item["why"] for item in out["items"])


def test_an_action_carries_the_size_of_the_thing_it_addresses():
    """Which is what lets the list be ordered at all. Without it the order
    is whichever order the model emitted, presented as a priority."""
    out = actions.build(CURRENT)
    poc = out["items"][0]
    assert poc["issue"] == "SHA rollout pain"
    assert poc["issue_share"] == pytest.approx(46.2, abs=0.2)
    shares = [i["issue_share"] or 0 for i in out["items"]]
    assert shares == sorted(shares, reverse=True), "not ordered by size"


def test_an_action_about_nothing_in_particular_says_so():
    """An action about a process or a platform may match no narrative, and
    a guessed match would print a size that is not that action's."""
    out = actions.build({"narratives": NARRATIVES,
                         "opportunities": ["Stand up a weekly publication cadence "
                                           "for whatever the next report needs."]})
    item = out["items"][0]
    assert item["issue"] is None
    assert item["movement_basis"] == "no_issue_matched"
    assert item["movement"] is None


def test_in_progress_means_it_was_on_the_last_list():
    first = actions.build(CURRENT)
    second = actions.build(CURRENT, {"narratives": NARRATIVES,
                                     "actions": first})
    carried = {i["action"]: i for i in second["items"]}
    for item in first["items"]:
        assert carried[item["action"]]["status"] == "carried"
        assert carried[item["action"]]["runs"] == 2, (
            "the number of reports an action has been carried for is not "
            "being counted")


def test_a_first_report_does_not_claim_an_issue_is_steady():
    """Reporting 0.0 points of movement against a report that does not
    exist is a claim about the subject drawn from a measurement nobody
    made."""
    out = actions.build(CURRENT)
    assert out["comparable"] is False
    for item in out["items"]:
        assert item["movement"] is None
        assert item["movement_basis"] in {"unknown", "no_issue_matched"}
    assert "first report" in out["note"]


def test_movement_is_two_stored_numbers_subtracted():
    previous = {"narratives": [
        {"label": "SHA rollout pain", "mentions": 140},
        {"label": "Taxation & cost of living", "mentions": 170},
        {"label": "Reform defense", "mentions": 90},
    ]}
    out = actions.build(CURRENT, previous)
    by_issue = {i["issue"]: i for i in out["items"]}

    # 140/400 = 35.0% then, 214/463 = 46.2% now.
    assert by_issue["SHA rollout pain"]["movement"] == pytest.approx(11.2, abs=0.2)
    assert by_issue["SHA rollout pain"]["movement_direction"] == "growing"
    assert by_issue["Taxation & cost of living"]["movement_direction"] == "shrinking"
    assert by_issue["Reform defense"]["movement_direction"] == "steady"


def test_a_tiny_share_is_not_reported_as_a_movement():
    """Two narratives at 0.4% and 0.9% are not a doubling of anything."""
    tiny = {"narratives": [{"label": "Fringe claim", "mentions": 1},
                           {"label": "Everything else", "mentions": 400}],
            "risks": ["A fringe claim about the Fringe claim narrative is "
                      "circulating and has not been answered anywhere."]}
    out = actions.build(tiny, {"narratives": [
        {"label": "Fringe claim", "mentions": 4},
        {"label": "Everything else", "mentions": 400}]})
    item = out["items"][0]
    assert item["movement"] is None
    assert item["movement_basis"] == "too_small"


def test_what_drops_off_the_list_is_stated():
    """An action that disappeared because the issue was settled and one
    that disappeared because the model forgot look identical in silence."""
    previous = {"narratives": NARRATIVES,
                "actions": {"items": [{"action": "Rebut the registration backlog claim",
                                       "runs": 1}]}}
    out = actions.build(CURRENT, previous)
    assert out["closed"] == ["Rebut the registration backlog claim"]


def test_no_action_carries_a_completion_figure():
    """What this replaced: the page drew a bar at 50% for anything whose
    status string was "in_progress" and 15% for everything else — a
    measurement with no referent, rendered as a measurement.

    Asserted on the data rather than by grepping for the word, because a
    file that explains why a number is absent naturally contains the word
    for it, and a check that trips on its own documentation is a check
    somebody deletes.
    """
    out = actions.build(CURRENT, {"narratives": NARRATIVES})
    forbidden = {"progress", "percent", "percent_complete", "completion",
                 "done", "pct"}
    for item in out["items"] + out["deferred"]:
        assert not (forbidden & set(item)), (
            f"{item['action']!r} carries a completion figure: "
            f"{sorted(forbidden & set(item))}")


def test_the_page_no_longer_draws_a_bar_from_a_status_string():
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    block = page[page.index("/* 2 — Actions in Progress."):
                 page.index("/* 3 — Tone (Sentiment). */")]
    assert "a.progress" not in block, "the invented progress bar is back"
    assert "'in_progress'?50" not in block.replace(" ", "")
    # And the honest replacements are there instead.
    assert "movement_basis" in block, (
        "the page does not say why a movement is missing, so 'no previous "
        "report' and 'this issue is steady' read the same")
    assert "runs" in block


# --- the shared sentence rule ------------------------------------------------

@pytest.mark.parametrize("text", [
    "A levy of 2.75% is deducted monthly. The service is unreliable.",
    "It was reported by nation.africa first. Nobody else carried it.",
    "See No. 3 on the schedule. It is the only clause that binds.",
    'The clinic said "SHA is not working today." He paid cash.',
    "Is it a tax or a benefit?! Nobody in the corpus answers that.",
    "Sh4.8 trillion in total. KSh 9.4 billion of it for settlement.",
    "e.g. the registration backlog. It remains unresolved.",
])
def test_splitting_sentences_never_loses_a_character(text):
    """`prose.sentences` is a split, so the pieces are the whole string by
    construction. The version it replaced in style_check.py matched
    sentences instead, and split "Sh4.8 trillion" into two — one of them
    four characters long — which matters there because the accusation check
    looks for an allegation and its attribution within ONE sentence.
    """
    pieces = prose.sentences(text)
    assert " ".join(" ".join(p.split()) for p in pieces) == " ".join(text.split())


def test_the_style_checker_uses_the_shared_splitter():
    body = open("engine/reports/style_check.py", encoding="utf-8").read()
    assert "prose.sentences(" in body
    assert not re.search(r"_SENTENCE\s*=\s*re\.compile", body), (
        "style_check has its own sentence pattern again")
