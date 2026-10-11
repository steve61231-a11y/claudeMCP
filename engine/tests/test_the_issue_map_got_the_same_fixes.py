"""Every presentation fix has reached one product and not the other.

The client said it once and it is worth keeping written down: "also goes
for the issue map as well, don't forget that." Both products run the same
analysts, are read by the same person, and are sold together — and this
codebase's history is a list of fixes that landed on the search report and
were never carried across. The layering test in
`test_the_report_is_layered.py` says the same thing about folding.

Two that had not been carried across, found by looking:

**Hand-typed section numbers.** The client's note on the search report was
"we need to number the sections properly"; it had two sections labelled
2.0 and a stray 4b. That was fixed there with a counter. The issue map
kept its own copy of the defect and shipped with TWO sections numbered 2
and no section 3 at all. A number nobody types cannot be typed twice.

**An influence figure with no denominator.** The report's Drivers section
used to read "influence 15.0" — a weighted sum of raw counts, unbounded
and not comparable between two subjects or two windows. That was replaced
with a share of the run's own total. The issue map was still printing bare
influence numbers in five places, from two different and undeclared
scales: whatever a model picked, or a percentage of the TOP actor, which
makes the leading actor read 100 in every report ever produced.
"""

import re

import pytest

from engine.api_server import render_frontend_document
from engine.reports import issue_framework

PAGE = render_frontend_document()


def _issue_framework_body() -> str:
    start = PAGE.index("function renderIssueFramework(")
    rest = PAGE[start + 10:]
    nxt = re.search(r"\n  function ", rest)
    return PAGE[start: start + 10 + (nxt.start() if nxt else len(rest))]


def test_no_section_number_in_the_issue_map_is_typed_by_hand():
    body = _issue_framework_body()
    typed = re.findall(r"fwSection\((['\"])(?!INPUT)([^'\"]+)\1", body)
    assert not typed, (
        "these section numbers are hand-typed, which is how the map ended "
        f"up with two 2s and no 3: {[t[1] for t in typed]}")
    assert "resetSections()" in body, (
        "the counter is not reset, so the map's numbering continues from "
        "whatever was rendered before it")


def test_the_input_markers_stay_outside_the_sequence():
    """"INPUT 1" and "INPUT 2" are the two things fed IN, not sections of
    the analysis, and numbering them would renumber everything after."""
    body = _issue_framework_body()
    assert body.count("fwSection('INPUT 1'") == 1
    assert body.count("fwSection('INPUT 2'") == 1


def test_every_stakeholder_influence_has_a_denominator():
    stakeholders = [
        {"name": "Ministry of Health", "influence": 100, "position": "for",
         "segment": "government"},
        {"name": "Patient advocates", "influence": 60, "position": "against",
         "segment": "civil_society"},
        {"name": "County assembly", "influence": 40, "position": "neutral",
         "segment": "government"},
    ]
    rows = issue_framework.with_influence_share(stakeholders)
    shares = [row["influence_share"] for row in rows]
    assert shares == [50.0, 30.0, 20.0]
    assert abs(sum(shares) - 100) < 0.2, (
        "the shares do not add up, so the column cannot be checked")


def test_a_stakeholder_with_no_influence_figure_gets_no_share():
    """Not a zero. A model that declined to score an actor and an actor
    with no influence are opposite findings."""
    rows = issue_framework.with_influence_share(
        [{"name": "Unknown actor", "position": "neutral"}])
    assert rows[0]["influence_share"] is None


def test_the_share_survives_into_every_section_that_shows_an_actor():
    built = issue_framework.build(
        "SHA", "William Ruto", {},
        [{"name": "Ministry of Health", "influence": 100, "position": "for",
          "segment": "government", "rationale": "Implementer"},
         {"name": "Patient advocates", "influence": 100, "position": "against",
          "segment": "civil_society", "rationale": "Claimant"}],
        [], [], desired_outcome="Stabilise the rollout")

    contours = built["main_contours"]["positions"]["for"]["segments"]["government"]
    assert contours[0]["influence_share"] == 50.0

    champion = built["stakeholder_networks"]["champions"][0]
    assert champion["influence_share"] == 50.0

    node = built["stakeholder_networks"]["visualisation"]["nodes"][0]
    assert node["share"] == 50.0


def test_the_page_never_prints_a_bare_influence_number():
    """Five sites did. One helper now answers "how much weight does this
    actor have", and where there is no share to show it says the figure is
    unscaled instead of presenting it as a measurement."""
    # A figure qualified as unscaled is the honest fallback, not a leak:
    # it is what `influenceChip` prints when the engine had no total to
    # take a share of, and saying so is the point.
    leaks = [m.group(0) for m in re.finditer(r"influence \$\{[^}]*\}", PAGE)
             if "unscaled" not in PAGE[m.end():m.end() + 140]]
    assert not leaks, (
        "a bare influence figure is still being rendered: " + "; ".join(leaks))
    assert "function influenceChip(" in PAGE
    assert "unscaled" in PAGE, (
        "there is no honest fallback, so an unscaled figure will read as a "
        "share")


def test_the_map_checks_its_names_against_the_corpus():
    """The third time a check in this codebase ran on one product and not
    the other, and the second time the map was the one left out — citations
    went the other way, triangulation went the other way.

    A map is MORE exposed than a report, not less. Its whole claim is a
    connection between two named things, so an invented third name sits in
    the middle of the sentence the client is paying for.
    """
    body = open("engine/reports/issue_map.py", encoding="utf-8").read()
    assert "grounding.check(" in body, (
        "the issue map does not check its names against the sources")
    assert 'publish("grounding"' in body, (
        "the result is computed and never sent, so the page cannot show it")
    assert '"grounding": grounding_result' in body, (
        "the stored map does not carry its grounding result, so a recovered "
        "map loses it")


def test_one_grounding_banner_serves_both_products():
    assert "function groundingBanner(" in PAGE
    assert "groundingBanner(wrap, r.grounding, 'report')" in PAGE
    assert "groundingBanner(host,m.grounding,'map')" in PAGE
    # And the third state. "We looked and found none", "we found some" and
    # "nobody looked" are three results, not two.
    banner = PAGE[PAGE.index("function groundingBanner("):]
    banner = banner[:banner.index("\n  }\n")]
    assert "gr.skipped" in banner, (
        "a check that did not run renders as a clean bill of health")
