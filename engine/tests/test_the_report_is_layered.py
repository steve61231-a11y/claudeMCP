"""The report says everything at the same volume.

Twenty sections stacked flat, each as prominent as the next, and the only
part that tells a reader to DO something — "What to do about it" — sat on
page 41 of a printed report between a platform breakdown and a
scoring-coverage table. A report is not long because it says too much; it is
long because nothing in it is ranked.

So the page has three zones. **The answer** is open: the finding, the brief,
the numbers that size it, and what to do. **The case** is the analysis
behind it, one folded line each. **The working** is provenance — what was
read, what was checked, what failed — which must be present and almost never
needs to be read.

The rule this must not break is the one the whole product is built on: a
section that failed must never look like a section with nothing to say. A
folded section is now a third state, so it has to be distinguishable from
both. Three things keep that true and are pinned below — every head carries
its own item count (including "nothing to show"), failures stay as banners
outside the folding mechanism entirely, and the printed file has everything
open, because paper has no chevrons to click.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.api_server import render_frontend_document

PAGE = render_frontend_document()
_FROM = PAGE.index("@media print{")
PRINT_BLOCK = re.sub(r"/\*.*?\*/", " ", PAGE[_FROM:PAGE.index("</style>", _FROM)], flags=re.S)


# --- the rule the product is built on ----------------------------------------

def test_a_printed_report_has_every_section_open():
    """Folding is a screen affordance. On paper a folded section is simply a
    missing one, which is the exact failure this codebase exists to prevent."""
    assert re.search(r"#zenith \.sect-body\{display:block!important\}", PRINT_BLOCK), (
        "folded sections will print as headings with no content")


def test_the_section_headings_survive_print():
    """`.sect-head` is a <button> because it toggles. The print block hides
    every button, which would have printed twenty bodies of text with no
    headings above them — the layering making the document WORSE on paper."""
    assert "button:not(.sect-head)" in PRINT_BLOCK, (
        "print hides all buttons, and the section headings are buttons")


def test_a_failed_section_is_not_foldable():
    """Run health, section failures, the test-grade warning and
    "nothing was collected" are built as plain banner cards in renderReport,
    above the first zone. If any of them ever became a `deepCard`, a reader
    could fold away the notice that the report is not trustworthy."""
    head = PAGE[PAGE.index("function renderReport("):PAGE.index("zone(wrap,'01'")]
    for banner in ("runHealth", "sectionStatus", "nothingCollected", "grade"):
        assert banner in head, f"the {banner} banner moved below the first zone"
    assert "deepCard" not in head, "a warning banner is now foldable"


def test_every_folded_head_says_how_much_is_inside():
    """Without a count, a section holding twenty dated events and one holding
    none are the same line of text — and "none" is a finding this report
    states out loud rather than hiding behind a chevron."""
    assert "labelSections" in PAGE
    body = PAGE[PAGE.index("function labelSections("):]
    body = body[:body.index("\n  }")]
    assert "nothing to show" in body, "an empty section is indistinguishable when folded"


def test_an_item_count_never_adds_containers_to_their_own_contents():
    """A combined selector counted Public Voice as "7 items": three stance
    columns PLUS the four themes inside them, which is not a number of
    anything."""
    body = PAGE[PAGE.index("function labelSections("):]
    body = body[:body.index("\n  }")]
    counts = re.search(r"const rows=(.*?);", body, re.S).group(1)
    assert "||" in counts and "," not in counts.split("||")[0], (
        "leaves and their containers are being counted together again")


# --- the layering itself ------------------------------------------------------

def test_the_report_declares_three_zones():
    for n, title in (("01", "The answer"), ("02", "The case"), ("03", "The working")):
        assert f"'{n}','{title}'" in PAGE, f"zone {n} {title} is gone"


def test_what_to_do_about_it_is_part_of_the_answer():
    """It is the only section that tells the reader to act, and it was
    rendered inside the deep read — page 41 of a printed report."""
    report = PAGE[PAGE.index("zone(wrap,'01'"):PAGE.index("renderDeepRead(wrap,r);")]
    assert "renderJudgementLists" in report, (
        "'What to do about it' is no longer in the answer zone")
    deep = PAGE[PAGE.index("function renderDeepRead("):]
    deep = deep[:deep.index("\n  }")]
    assert "renderJudgementLists" not in deep, "it is being rendered twice"


def test_the_issue_map_is_layered_too():
    """Both products run the same analysts and are read by the same person.
    Every previous presentation fix reached one and not the other."""
    assert "layerIssueMap" in PAGE
    assert "intersectionDetail(host,m); layerIssueMap(host);" in PAGE


def test_an_unrecognised_issue_map_card_stays_open():
    """The map's warnings — "the record does not connect these two",
    "counted, not read", a discovery sweep that did not run — are cards like
    any other. A zone table that folded anything it did not recognise would
    fold exactly those."""
    body = PAGE[PAGE.index("function layerIssueMap("):]
    body = body[:body.index("\n  }")]
    assert "else answer.push(card)" in body, (
        "an unknown issue-map card is no longer treated as part of the answer")


# --- what it actually renders -------------------------------------------------

CHROME = None
try:
    from engine.reports import pdf_export
    CHROME = pdf_export.chrome_path()
except Exception:
    pass

requires_chrome = pytest.mark.skipif(CHROME is None, reason="no Chromium here")


def _print_demo(tmp_path: Path) -> str:
    boot = ("<script>window.addEventListener('load', function(){"
            " window.ZENITH.renderReport(document.getElementById('view'),"
            " window.ZENITH.DEMO.report, {live:false}); });</script>")
    cut = PAGE.rfind("</body>")
    src = tmp_path / "r.html"
    src.write_text(PAGE[:cut] + boot + PAGE[cut:], encoding="utf-8")
    out = tmp_path / "r.pdf"
    subprocess.run(
        [str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--hide-scrollbars",
         "--virtual-time-budget=9000", f"--print-to-pdf={out}",
         "--no-pdf-header-footer", str(src)],
        capture_output=True, timeout=180)
    return subprocess.run(["pdftotext", "-layout", str(out), "-"],
                          capture_output=True, text=True).stdout


@requires_chrome
def test_the_printed_file_still_contains_the_folded_sections(tmp_path):
    """The assertion that matters. Everything below is a heading that is
    folded on screen — if layering ever starts hiding content from the PDF,
    this is where it shows up."""
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    text = _print_demo(tmp_path)
    for zone in ("THE ANSWER", "THE CASE", "THE WORKING"):
        assert zone in text, f"the {zone} band is missing from print"
    for heading in ("BENEATH THE SURFACE", "NARRATIVES", "PUBLIC VOICE",
                    "CHECK THE WORK", "REPRESENTATIVE MENTIONS", "STORYLINE"):
        assert heading in text, f"{heading} is folded out of the printed report"

    # The headings are NOT the assertion. They live in `.sect-head`, which is
    # outside the element that folds — so checking only those passed happily
    # with the print override deleted and every BODY missing, which is the
    # whole defect. Assert on text that only exists inside a folded body.
    for inside in ("SHA rollout is the true center",   # Beneath the surface
                   "Registered, paid, walked",         # a Public Voice quote
                   "Comment sections are markedly"):   # Platform pulse
        assert inside in text, (
            f"{inside!r} is inside a folded section and did not reach the "
            "printed file — layering is hiding content from the PDF")
