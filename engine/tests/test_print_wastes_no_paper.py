"""Why a 66-page report was 66 pages.

Three defects in the print stylesheet, all of them the same mistake at
different depths: **atomicity applied to a container instead of a leaf.**

`break-inside:avoid` tells the browser to move an element whole rather than
split it. Applied to something small — a quote — that is right, because half
a quote is unreadable. Applied to something large, it is a page-waster: the
element cannot fit in what is left, so it moves to the next page and
everything above it stays blank.

It was applied to `.card`. The report's cards are its sections, and "What
happened, and when" runs to twenty dated entries, so every long section
started on a fresh page. Measured on the demo report: 9 pages at 1,099
characters each, three of them under 900, one of them 346.

Fixing that and protecting `.stance-col` instead moved the blank space one
level down — a bordered, three-quarters-empty column alone on a page. Fixing
THAT and protecting `.mentr` moved it down again, because a `.mentr` in
Public Voice is a theme heading plus a 150-word summary plus six quotes.

The same report also printed the sentiment split as three labels and three
percentages on an otherwise empty page. Every bar in this app is a div whose
only visual is a background colour, and a browser's print dialog defaults
"Background graphics" to off — which is how the app's own Download PDF
prints. A chart that failed to print looked exactly like a chart with no
data. That is the one thing this product exists not to do.
"""

import re

import pytest

from engine.api_server import render_frontend_document

PAGE = render_frontend_document()
#: The page has several <style> elements, and the first `</style>` comes well
#: before the print rules — slicing to it gave an EMPTY block that every
#: assertion below then read as "the rule is missing".
_FROM = PAGE.index("@media print{")
PRINT_BLOCK = re.sub(r"/\*.*?\*/", " ",
                     PAGE[_FROM:PAGE.index("</style>", _FROM)], flags=re.S)


def _selectors_with(declaration: str) -> set[str]:
    """Every selector in the print block carrying `declaration`."""
    found = set()
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", PRINT_BLOCK):
        if declaration in re.sub(r"\s+", "", rule.group(2)):
            found.update(s.strip() for s in rule.group(1).split(","))
    return found


ATOMIC = _selectors_with("break-inside:avoid")


@pytest.mark.parametrize("container", [".card", ".stance-col", ".mentr", ".grid"])
def test_a_container_is_never_made_unbreakable(container):
    """Each of these was protected in turn and each cost pages. The question
    to ask before adding to that rule is not "is this a row" but "is this
    still readable if a page break falls inside it" — for anything that holds
    a list of other things, the answer is yes."""
    offenders = {s for s in ATOMIC if s.endswith(container)}
    assert not offenders, (
        f"{container} is unbreakable in print ({offenders}) — it holds other "
        "elements, so it will be moved whole and strand the space above it")


def test_the_leaves_that_must_not_split_still_do_not():
    """The other half of the rule. A quote cut across a page break is
    unreadable, and a chart split down the middle is worse than absent."""
    for leaf in (".qt", ".chart-box", ".chart-ring"):
        assert any(s.endswith(leaf) for s in ATOMIC), f"{leaf} may now be split"


def test_a_heading_is_not_left_alone_at_the_foot_of_a_page():
    keep = _selectors_with("break-after:avoid")
    assert any(s.endswith(".lbl") for s in keep)
    # The heading is two elements — a label and the line under it — so
    # holding only the label still strands both.
    assert any(".lbl + .muted" in s or ".lbl+.muted" in s for s in keep), (
        "a section's sub-heading can still be orphaned from its content")


def test_a_bar_chart_prints_its_bars():
    """`print-color-adjust:exact` is what overrides the print dialog's
    "Background graphics: off". Without it every bar in the report is
    invisible and the page shows labels with nothing beside them."""
    exact = _selectors_with("print-color-adjust:exact")
    flat = " ".join(exact)
    for part in (".bar", ".hbar-fill", "svg"):
        assert part in flat, f"{part} will not print its colour"


def test_the_empty_part_of_a_bar_is_visible_on_paper():
    """The track is `rgba(255,255,255,.06)` on screen — white on white once
    printed, so a 95% bar and a 5% bar are the same length: no length at
    all."""
    tracks = _selectors_with("background:#e6e8eb!important")
    flat = " ".join(tracks)
    assert ".bar" in flat and ".hbar-track" in flat


def test_stances_are_not_three_narrow_columns_on_paper():
    """Chromium does not fragment grid rows. Three stances side by side is a
    grid row that either fits or moves whole, taking a page with it."""
    assert re.search(r"#zenith \.stance\s*\{[^}]*display:\s*block", PRINT_BLOCK), (
        "the stance grid will not fragment and will strand a page")
