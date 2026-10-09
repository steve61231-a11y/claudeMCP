"""Three changes asked for by the person selling this to executives.

**Counts became shares.** "214 mentions" is a number nobody can act on: 214
out of what? The reader has to find the total, divide, and only then knows
whether the narrative is the story or a footnote — work done once per row by
somebody deciding in minutes. The proportion is now the headline and the
count is the tooltip. Nothing is hidden; it is reordered by what a decision
needs.

**Dates became British.** ISO ("2026-09-09") is unambiguous and unreadable.
Slashed ("09/07/2026") is readable and ambiguous — an American and a Kenyan
reader get different days out of it, and this file gets forwarded. An ordinal
day with a named month cannot be misread by either.

**Emerging issues became three, with a take.** The prompt asked for "typically
6-12 ... not three", and twelve items of 2-4 sentences is a page and a half
nobody reads. Twelve things to watch is also not a priority order — it is a
list that refused to choose, and choosing is the work. Each item returned a
bare paragraph that opened with what happened, so the reader reached the end
of every one before finding the part they needed; `issue` and `take` are now
separate fields, which forces the model to name the thing and then commit to a
reading of it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "web" / "pulse_app.html").read_text()

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="no node here")


def _run(calls):
    """Evaluate the page's own helpers, so these pin the shipped code."""
    script = PAGE.split("<script>", 1)[1].rsplit("</script>", 1)[0]

    def grab(start, end):
        return script[script.index(start):script.index(end)]

    # Lift the page's own esc/fmt rather than redefining them: the ukWindow
    # span below already contains fmt, and declaring it twice is a syntax
    # error that fails every test in the file for a reason unrelated to any
    # of them.
    harness = (
        grab("const esc = ", "  /* ---")
        + grab("function share(n, of, noun)", "const _MONTHS")
        + grab("function ukWindow(value)", "  const pct =")
        + "console.log(JSON.stringify([" + ",".join(calls) + "]));"
    )
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# --- shares -------------------------------------------------------------------

@requires_node
def test_a_count_is_shown_as_a_share_of_the_corpus():
    got, = _run(["share(214, 1120)"])
    assert ">19%<" in got


@requires_node
def test_the_real_count_survives_in_the_tooltip():
    """Reordered, not hidden. An executive reads the proportion; anybody
    checking the work still gets the figure."""
    got, = _run(["share(214, 1120)"])
    assert 'title="214 of 1,120 mentions"' in got


@requires_node
def test_without_a_total_it_shows_the_count_rather_than_inventing_a_share():
    """A percentage computed against a missing denominator is a fabricated
    number, which is precisely what this product must not print."""
    assert _run(["share(5, 0)"]) == ["5 mentions"]
    assert _run(["share(5, null)"]) == ["5 mentions"]


@requires_node
def test_a_missing_count_stays_missing():
    assert _run(["share(null, 100)"]) == ["\u2014"]


def test_no_render_site_prints_a_bare_count_of_mentions():
    """The helpers being right proves nothing if a renderer does not call them.

    Reverting one site to `${fmt(n.mentions)} mentions` passed every other
    test in this file, which is the whole failure mode of testing a helper
    instead of its use. This greps the shipped page for a count rendered
    straight into the words "mentions".
    """
    leaks = [m.group(0).strip() for m in re.finditer(
        r"\$\{fmt\([^}]*?\b(?:mentions|mention_count)\b[^}]*?\)\}\s*mentions", PAGE)]
    assert not leaks, (
        f"a raw mention count is still being rendered: {leaks} — use "
        "share(n, CORPUS_TOTAL)")


def test_the_page_has_a_denominator_to_divide_by():
    """The renderers showing counts sit several levels below the payload. A
    share helper they cannot feed is a share helper that always falls back."""
    assert "CORPUS_TOTAL" in PAGE
    assert "p.volume && p.volume.total" in PAGE


# --- dates --------------------------------------------------------------------

@requires_node
@pytest.mark.parametrize("iso,expected", [
    ("2026-09-09", "9th Sept 2026"),
    ("2026-09-01", "1st Sept 2026"),
    ("2026-09-02", "2nd Sept 2026"),
    ("2026-09-03", "3rd Sept 2026"),
    ("2026-09-11", "11th Sept 2026"),   # not 11st
    ("2026-09-12", "12th Sept 2026"),   # not 12nd
    ("2026-09-13", "13th Sept 2026"),   # not 13rd
    ("2026-09-21", "21st Sept 2026"),
    ("2026-07-09T10:11:00", "9th Jul 2026"),
])
def test_a_date_reads_the_same_in_nairobi_and_new_york(iso, expected):
    assert _run([f'ukDate("{iso}")']) == [expected]


@requires_node
def test_an_unparseable_date_is_passed_through_not_mangled():
    """"undated" is a real value in this payload and must not become a date."""
    assert _run(['ukDate("undated")']) == ["undated"]
    assert _run(['ukDate("")']) == ["\u2014"]


@requires_node
def test_a_window_reformats_both_ends():
    got, = _run(['ukWindow("2025-08-01 \\u2013 2026-07-13")'])
    assert got == "1st Aug 2025 \u2013 13th Jul 2026"


def test_no_date_is_rendered_by_truncating_it():
    """The specific regression: one forgotten `.slice(0,10)` on a timestamp
    and an ISO date goes out in a format the reader was told would not appear.

    Only date-bearing expressions count. `rows.slice(0, 10)` takes the first
    ten rows and has nothing to do with this — a blunter check flagged those
    and would have to be ignored, which is how a check stops working.
    """
    # Exclude ukDate itself: its last resort for a value no parser accepts is
    # to truncate, which is the behaviour this test protects rather than an
    # instance of the bug.
    body = PAGE
    start = body.index("function ukDate(value)")
    end = body.index("\n  }", start)
    body = body[:start] + body[end:]

    leaks = [m.group(0).strip() for m in
             re.finditer(r"[^\n]*\b(?:date|posted_at|generatedAt|_at)\b[^\n]*?"
                         r"\.slice\(0,\s*10\)", body, re.IGNORECASE)]
    assert not leaks, f"a date is still being rendered by truncation: {leaks}"


# --- three issues, each with a take -------------------------------------------

def test_only_three_emerging_issues_reach_the_page():
    from engine.reports import sections

    assert sections.MAX_TRENDS == 3


def test_the_prompt_asks_for_three_and_for_the_take():
    from engine.reports.sections import TRENDS_PROMPT

    flat = re.sub(r"\s+", " ", TRENDS_PROMPT)
    assert "THREE most important" in flat
    assert '"take"' in flat
    assert '"reaction"' in flat
    assert "Not six, not twelve" in flat


def test_more_than_three_are_cut(monkeypatch):
    from engine.reports import sections

    monkeypatch.setattr(sections.llm, "call_json", lambda *a, **k: {"trends": [
        {"issue": f"issue {i}", "take": "t"} for i in range(9)]})
    assert len(sections.generate_trends("ctx")) == 3


def test_an_older_report_of_bare_strings_still_renders(monkeypatch):
    """Stored reports hold strings, and a model can always ignore a schema.
    A trend the reader cannot see is worse than one without a take."""
    from engine.reports import sections

    monkeypatch.setattr(sections.llm, "call_json", lambda *a, **k: {
        "trends": ["Comment sentiment is diverging from broadcast framing."]})
    out = sections.generate_trends("ctx")
    assert out and out[0]["issue"]


def test_an_empty_row_is_dropped_rather_than_rendered_blank(monkeypatch):
    from engine.reports import sections

    monkeypatch.setattr(sections.llm, "call_json", lambda *a, **k: {
        "trends": [{"issue": "", "take": ""}, "", {"issue": "real one"}]})
    out = sections.generate_trends("ctx")
    assert [row["issue"] for row in out] == ["real one"]


def test_the_page_renders_the_take_and_not_just_the_label():
    assert "rowOf(" in PAGE
    assert "item.issue || item.take" in PAGE
    assert "item.reaction" in PAGE
