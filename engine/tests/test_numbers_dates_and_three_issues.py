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


def test_every_bar_chart_is_scaled_to_something_it_is_a_share_of():
    """A bar chart with no denominator always has one full-width bar.

    `hBars` scales to the largest value present unless it is told what the
    whole is. So a source with 60% of the coverage and a source with 6% of
    it drew IDENTICALLY whenever each happened to lead its own section —
    which is how three charts on one page could not be compared with each
    other, and what the client's note on the media graph was about:
    "this graph needs to reflect mentions as percentage of total as with
    the others, same with 6 and 7 below."

    Pinned at the call sites rather than on the helper, because the helper
    was already capable of this and the charts simply were not asking. A
    new chart that forgets is the same bug again, so the rule is: every
    `hBars` call passes a total.
    """
    calls = [m for m in re.finditer(r"hBars\(", PAGE)]
    # Skip the definition itself.
    sites = []
    for match in calls:
        if PAGE[max(0, match.start() - 9):match.start()].endswith("function "):
            continue
        # The call's own argument list, to its closing paren.
        depth, i = 0, match.end() - 1
        while i < len(PAGE):
            if PAGE[i] == "(":
                depth += 1
            elif PAGE[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        sites.append(PAGE[match.start():i + 1])

    assert sites, "no hBars call sites found — has the helper been renamed?"
    missing = [s[:90].replace("\n", " ") for s in sites
               if "total:" not in s and "suffix:'%'" not in s]
    assert not missing, (
        "these bar charts scale to their own largest bar, so one of them is "
        "always full width whatever it is a share of: " + "; ".join(missing))


def test_a_bar_chart_of_counts_prints_the_share_not_the_count():
    """The number at the end of the bar is what a reader quotes. The count
    belongs in the tooltip, where it is checkable, and the share belongs on
    the page, where it is comparable."""
    for needle in ("display:share(sgm.count,segTotal)",
                   "display:share(sent.positive,sentTotal)"):
        assert needle in re.sub(r"\s+", "", PAGE), (
            f"{needle} is not on the page — a bar is labelled with a raw count")


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


def test_every_date_on_the_page_goes_through_a_formatter():
    """The truncation check above catches one spelling of the bug. This
    catches the category.

    Three render sites drew `item.date` straight out of the payload, so the
    same moment read "9th Jul 2026" in the detail panel and "2026-07-09" in
    the hover tooltip and in the screen-reader label beside it. The client
    asked for British dates once; an ISO date surviving in a tooltip is the
    same defect as an ISO date in a heading, only somewhere nobody looked.

    `asWritten` is the one permitted exception and says so at its
    definition: an analyst's own "Q1 2026" is not a date to reformat.
    """
    formatters = ("ukDate", "ukWindow", "whenLabel", "shortDate", "whenOf",
                  "asWritten")
    leaks = sorted({
        m.group(0)
        for m in re.finditer(r"\$\{[^{}]*\}", PAGE)
        # The date-bearing token must be the LAST property in the chain:
        # `o.first_seen.platform` is a platform, not a date, and a check
        # that flags it is a check somebody will start ignoring.
        if re.search(r"\.(date|when|posted_at|published_at|generatedAt"
                     r"|first_seen|newestMentionAt)\b(?!\s*\.)", m.group(0))
        and not any(f in m.group(0) for f in formatters)
    })
    assert not leaks, (
        "these render a date straight from the payload: " + "; ".join(leaks))
