"""A timeline that was not one.

The strip spaced its points evenly by INDEX. Three things in one week and a
fourth six months later drew identically — so the picture could not show the
one thing a timeline exists to show, which is the shape of a window: a quiet
month, then everything at once. It was a numbered list wearing a graph.

It also threw away `mentions_that_day`, which the pipeline has always
produced. Where the conversation actually spiked is the first question anyone
asks of a window, and the answer was sitting in the payload, undrawn.

The rule it must not break is the usual one. An event the axis cannot place —
"Q1 2026", "undated", a month with no day — must not simply be absent from
the picture, because a reader cannot tell a dropped event from an event that
does not exist. Undated moments are counted and disclosed.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.api_server import render_frontend_document

PAGE = render_frontend_document()
_FROM = PAGE.index("@media print{")
PRINT_BLOCK = re.sub(r"/\*.*?\*/", " ", PAGE[_FROM:PAGE.index("</style>", _FROM)], flags=re.S)


def _extract(name: str) -> str:
    """Lift one declaration out of the page's IIFE, balanced-bracket exact."""
    script = PAGE.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    start = script.index(name)
    close = {")": "(", "}": "{", "]": "["}
    depth = {"(": 0, "{": 0, "[": 0}
    opened = False
    for i in range(start, len(script)):
        ch = script[i]
        if ch in "({[":
            depth[ch] += 1
            opened = True
        elif ch in ")}]":
            depth[close[ch]] -= 1
            if ch == "}" and opened and not any(depth.values()):
                return script[start:i + 1]
    raise AssertionError(f"could not find the end of {name!r}")


requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="no node here")


def _run(calls):
    harness = _extract("function whenOf(") + "\nconsole.log(JSON.stringify([" + ",".join(calls) + "]));"
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- placing a moment in time -------------------------------------------------

@requires_node
def test_a_full_date_is_placed_on_its_day():
    got, = _run(['whenOf({date:"2026-06-02"})'])
    assert got == 1780358400000  # 2026-06-02T00:00:00Z


@requires_node
def test_a_partial_date_is_placed_in_the_middle_of_what_it_names():
    """"June 2026" is not the 1st of June. Placing it there would assert a
    precision the source does not have, and on a tight axis that is the
    difference between "before the announcement" and "after"."""
    month, quarter, year = _run(['whenOf({date:"2026-06"})',
                                 'whenOf({when:"Q1 2026"})',
                                 'whenOf({date:"2026"})'])
    import datetime as dt
    as_day = lambda ms: dt.datetime.utcfromtimestamp(ms / 1000).date().isoformat()
    assert as_day(month) == "2026-06-15"
    assert as_day(quarter) == "2026-02-15"      # middle of Jan-Mar
    assert as_day(year) == "2026-07-01"


@requires_node
def test_a_moment_with_no_usable_date_is_not_guessed_at():
    for raw in ('{date:"undated"}', '{date:""}', '{}', '{date:"soon"}'):
        got, = _run([f"whenOf({raw})"])
        assert got is None, f"{raw} was given a position on the axis"


@requires_node
def test_the_issue_maps_own_date_field_is_read():
    """The map writes `when` ("Q1 2026"), the report writes `date`. One graph
    serves both, so it has to read both."""
    got, = _run(['whenOf({when:"2026-06-02"})'])
    assert got is not None


# --- what the graph must not do ----------------------------------------------

STRIP = _extract("function interactiveTimelineStrip(")


def test_points_are_positioned_by_date_not_by_index():
    """The defect. `idx/(length-1)` is still there as the fallback for when
    there are not enough distinct dates to scale by, but it must not be the
    primary path."""
    assert "(p.ms-lo)/(hi-lo)" in STRIP, "the axis is not scaled by time"
    assert "timeScaled" in STRIP


def test_a_window_with_one_date_falls_back_and_says_so():
    """Every moment on one day would stack every marker on one x. Spacing
    them evenly is fine; doing it silently is not, because the picture then
    implies a tempo the data does not have."""
    assert "Not enough distinct dates to scale by time" in STRIP


def test_undated_moments_are_counted_not_dropped():
    """A source-grep only, and a weak one — it passes with the branch that
    emits it disabled. The real assertion is in the rendered test at the
    bottom of this file, which puts an undated moment in the payload and
    requires the page to say so."""
    assert "could not be dated" in STRIP


def test_volume_is_drawn():
    assert "mentions_that_day" in STRIP and "tl-vol" in STRIP


def test_a_marker_is_coloured_by_corroboration_not_decoration():
    """Green means more than one independent outlet reported it. That signal
    has to come from the payload, or the colour is asserting corroboration
    nobody computed."""
    assert "tc.confirmed?'is-confirmed':'is-single'" in STRIP


def test_the_graph_is_reachable_without_a_mouse():
    assert "tabindex" in STRIP and "role" in STRIP
    assert "Enter'||e.key===' '" in STRIP, "markers cannot be activated by keyboard"


# --- print --------------------------------------------------------------------

def test_the_printed_list_is_not_truncated():
    """On screen the list is an index — two lines each — because the open
    moment is shown in full in the panel above it. On paper there is no
    panel and no moment is open, so a clamp would print twenty stubs."""
    assert "-webkit-line-clamp:unset!important" in PRINT_BLOCK


def test_the_detail_panel_does_not_print():
    """With the whole list printed in full, the panel is one of those entries
    a second time. It exists to save a click and there are no clicks here."""
    assert re.search(r"#zenith \.tl-detail[^{]*\{display:none!important\}", PRINT_BLOCK)


def test_a_mouse_instruction_does_not_print_but_the_disclosure_does():
    """"Click a point" is noise on paper. "3 moments could not be dated" is
    not — it is the honesty rule, and it has to survive."""
    assert ".tl-onscreen" in PRINT_BLOCK
    hint = STRIP[STRIP.index("could not be dated") - 400:STRIP.index("could not be dated")]
    assert "tl-onscreen" not in hint, "the undated disclosure is marked screen-only"


# --- end to end ---------------------------------------------------------------

CHROME = None
try:
    from engine.reports import pdf_export
    CHROME = pdf_export.chrome_path()
except Exception:
    pass


@pytest.mark.skipif(CHROME is None, reason="no Chromium here")
def test_the_graph_renders_and_opens_a_moment(tmp_path):
    """Drive the real page: build a window with a quiet stretch and a spike,
    and assert the markers land in proportion to their dates rather than in
    equal steps."""
    boot = """<script>window.addEventListener('load',function(){
      var r=window.ZENITH.DEMO.report;
      r.timeline=[{date:'2026-01-01',event:'first',mentions_that_day:5},
                  {date:'2026-01-08',event:'second',mentions_that_day:9},
                  {date:'2026-12-31',event:'last',mentions_that_day:100},
                  {date:'undated',event:'no date here'}];
      window.ZENITH.renderReport(document.getElementById('view'),r,{live:false});
      document.querySelectorAll('.sect').forEach(s=>s.setAttribute('open',''));
      var xs=[...document.querySelectorAll('.tl-strip-node circle')]
              .map(c=>Math.round(+c.getAttribute('cx')));
      document.title=JSON.stringify({xs:xs,
        detail:!!document.querySelector('.tl-detail'),
        hints:[...document.querySelectorAll('.tl-strip-hint')].map(h=>h.textContent).join(' | ')});
    });</script>"""
    cut = PAGE.rfind("</body>")
    src = tmp_path / "g.html"
    src.write_text(PAGE[:cut] + boot + PAGE[cut:], encoding="utf-8")
    dom = subprocess.run(
        [str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--virtual-time-budget=9000",
         "--dump-dom", str(src)], capture_output=True, text=True, timeout=180).stdout
    state = json.loads(re.search(r"<title>(.*?)</title>", dom, re.S).group(1))

    xs = state["xs"]
    assert len(xs) == 3, f"expected 3 dated markers on the axis, got {xs}"
    # 1 Jan, 8 Jan, 31 Dec. Evenly spaced by index the middle marker would sit
    # halfway across; by date it sits within a few percent of the left edge.
    first, second, last = xs
    position = (second - first) / (last - first)
    assert position < 0.05, (
        f"the 8 January marker sits {position:.0%} of the way across a year — "
        "the axis is spacing by index, not by date")
    assert state["detail"], "no detail panel was opened"
    assert "Spaced by real date" in state["hints"]
    # The payload carries four moments and only three can be placed. A reader
    # who is not told that cannot tell a dropped event from one that does not
    # exist — which is the rule the whole product is built on.
    assert "1 moment could not be dated" in state["hints"], (
        f"the undated moment vanished without a word: {state['hints']!r}")
