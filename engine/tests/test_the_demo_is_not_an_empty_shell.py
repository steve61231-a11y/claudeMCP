"""The demo is the sales asset, and it rendered as a page of empty states.

`DEMO.report` carried the corpus — mentions, narratives, quotes, the
timeline — and none of the dashboard's own data: no `sentiment_framework`,
no `periodSeries`, no `influence`, no `actions`. So the first thing a
prospective client saw was "no sentiment scored yet", "no narratives
identified", "no scored mentions to chart movement between periods", eight
sections deep.

That is this product's one forbidden failure pointed at the buyer. A
dashboard that works and a dashboard that was never wired up looked
identical, and the one place that comparison gets made is a sales meeting.

Two things are pinned here.

**Every key the dashboard reads is present.** Derived from the renderer
rather than hand-listed, because a hand-written field list is what let raw
citations leak through four rounds of fixes in this codebase. Add a
`r.something` to the dashboard and this test tells you the demo has a hole.

**The arithmetic reconciles.** The demo's numbers are the ones a buyer
checks: tone across the periods sums to the headline split, each theme's
periods sum to that narrative's own mention count, and every impact is
(coverage + engagement) / 2. A demo that contradicts itself under a pen
and paper is worse than a thin one.
"""

import json
import re
import shutil
import subprocess

import pytest

from engine.api_server import render_frontend_document

PAGE = render_frontend_document()


def _dashboard_body() -> str:
    start = PAGE.index("function renderWeeklyDashboard")
    rest = PAGE[start + 10:]
    nxt = re.search(r"\n  function ", rest)
    return PAGE[start: start + 10 + (nxt.start() if nxt else len(rest))]


def _demo_report() -> dict:
    """The DEMO literal, as data.

    Evaluated rather than regex-scraped: it is a JavaScript object literal
    with unquoted keys and one helper call (`normNet`), which no amount of
    string handling turns into reliable JSON.
    """
    if not shutil.which("node"):
        pytest.skip("node not installed")
    start = PAGE.index("const DEMO={")
    end = PAGE.index("\n  };", start) + len("\n  }")
    literal = PAGE[start + len("const DEMO="): end]
    script = ("const normNet=x=>x;const DEMO=" + literal
              + ";process.stdout.write(JSON.stringify(DEMO.report));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


#: Reads that are a renderer's own bookkeeping rather than report content.
_NOT_DATA = {"length", "map", "filter", "slice", "forEach", "reduce"}


def test_the_dashboard_finds_something_for_every_field_it_reads():
    body = _dashboard_body()
    report = _demo_report()

    #: Group the reads by the `||` chain they sit in: `r.actions||r.recommendations`
    #: is satisfied by either, and demanding both would be demanding the demo
    #: carry two spellings of one thing.
    # Local aliases count as reads of what they alias. The dashboard opens
    # with `const act=r.actions||{}` and then reads `act.items`, so a scan
    # for `r.<key>` alone cannot see that the fallback chain two lines
    # later is already satisfied — and demanded the demo carry a legacy key
    # that exists only for reports generated before this feature.
    aliases = dict(re.findall(r"const\s+(\w+)\s*=\s*r\.(\w+)", body))

    missing = []
    # Grouped per STATEMENT, not per line. `const x = r.actions
    # || r.recommendations || …;` is one fallback chain however it happens
    # to be wrapped, and a line-based reading of it demanded that the demo
    # carry every spelling of one thing.
    for statement in re.split(r";", body):
        keys = [k for k in re.findall(r"\br\.(\w+)", statement)
                if k not in _NOT_DATA]
        for local, key in aliases.items():
            if re.search(r"\b" + local + r"\.", statement):
                keys.append(key)
        if not keys:
            continue
        groups = [keys] if "||" in statement else [[k] for k in keys]
        for group in groups:
            if not any(report.get(k) not in (None, [], {}, "") for k in group):
                missing.append(" || ".join("r." + k for k in group))

    assert not missing, (
        "the demo dashboard will render an empty state for: "
        + ", ".join(sorted(set(missing))))


def test_the_demo_tone_periods_add_up_to_its_headline_split():
    report = _demo_report()
    tone = report["periodSeries"]["tone"]
    sentiment = report["sentiment"]
    total = sentiment["totalAnalyzed"]

    assert sum(p["mentions"] for p in tone) == total, (
        "the period series and the headline mention count disagree")
    for key in ("positive", "neutral", "negative"):
        counted = sum(p["values"][key] for p in tone)
        assert round(100 * counted / total) == sentiment[key], (
            f"{key} across the periods is {round(100 * counted / total)}% "
            f"but the headline says {sentiment[key]}%")


def test_the_demo_themes_add_up_to_their_narratives():
    report = _demo_report()
    themes = report["periodSeries"]["themes"]
    declared = {n["label"]: n["mentions"] for n in report["narratives"]}

    for label in themes["keys"]:
        counted = sum(p["values"][label] for p in themes["periods"])
        assert counted == declared[label], (
            f"{label} is {declared[label]} mentions in the narrative list "
            f"and {counted} across the periods")


def test_every_demo_impact_is_coverage_plus_engagement():
    """The definition the client gave, and the whole reason the Drivers
    section can be ranked at all. A demo row that does not satisfy it
    teaches a buyer the wrong arithmetic."""
    for row in _demo_report()["influence"]:
        expected = round((row["coverage_share"] + row["engagement_share"]) / 2, 1)
        assert abs(row["impact"] - expected) < 0.06, (
            f"{row['who']}: impact {row['impact']} is not "
            f"({row['coverage_share']} + {row['engagement_share']}) / 2")


def test_the_demo_drivers_are_ranked_by_impact():
    impacts = [row["impact"] for row in _demo_report()["influence"]]
    assert impacts == sorted(impacts, reverse=True), (
        "the demo's drivers are not in impact order, so the section's own "
        "claim to be ranked is false on the page a buyer is shown")
