"""Three separate ways of hiding things, three separate print bugs.

The report collapses content in three different mechanisms, and each one
needed the same fix, discovered separately, months apart:

  `.sect` with an `open` attribute        — the twenty report sections
  `details.fold`                          — the blocks inside a section
  `.drill-body` with a `hidden` attribute — "show the 4 mentions behind this"

Each is correct on screen. Each hides its content on paper too, unless the
print block goes out of its way to overrule it. The first was fixed when
layering shipped. The second shipped broken, with a code comment claiming
it was handled. The third printed the heading "Sources covered (6)" with
not one source underneath it — measured: zero of six sources' quotes
reached the exported PDF. That is the provenance section of a
due-diligence report printing empty.

So this file does not test the three mechanisms. It tests the outcome, in a
real browser, against the real demo report: a string that is only reachable
by opening something must still appear in the printed file. A fourth
mechanism added later fails this without anyone having to remember to come
back and extend a list.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.api_server import render_frontend_document

PAGE = render_frontend_document()

CHROME = None
try:
    from engine.reports import pdf_export
    CHROME = pdf_export.chrome_path()
except Exception:
    pass

requires_chrome = pytest.mark.skipif(CHROME is None, reason="no Chromium here")


def _printed(tmp_path: Path) -> str:
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
         "--virtual-time-budget=11000", f"--print-to-pdf={out}",
         "--no-pdf-header-footer", str(src)],
        capture_output=True, timeout=240)
    return subprocess.run(["pdftotext", "-layout", str(out), "-"],
                          capture_output=True, text=True).stdout


#: One string per hiding mechanism, each reachable on screen only by
#: opening something, and each a different mechanism from the others.
BURIED = {
    "a collapsed section (.sect / [open])":
        "SHA rollout is the true center",
    "a folded block (details.fold)":
        "Oncology unit says approval delays",
    "a receipts body ([hidden])":
        "Over 4.2 million members now registered",
    "a presentation that is not the open tab (.mode-off)":
        "What to act on, what it is about",
    "evidence under a current-issues card (nested fold)":
        "A levy collected reliably that delivers unreliably",
}


@requires_chrome
def test_everything_the_page_can_hide_still_prints(tmp_path):
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    text = " ".join(_printed(tmp_path).split())
    missing = {where: needle for where, needle in BURIED.items()
               if needle not in text}
    assert not missing, (
        "content reachable only by opening something did not reach the "
        "printed file: " + "; ".join(f"{w} ({n!r})" for w, n in missing.items()))


def test_each_known_hiding_mechanism_has_a_print_override():
    """The cheap version of the test above, so a CSS mistake is caught
    without Chromium. Not a substitute for it: this checks that a rule
    exists, not that it works — `details:not([open]) > *` looked exactly
    like a fix and does nothing at all."""
    start = PAGE.index("@media print{")
    block = PAGE[start:PAGE.index("</style>", start)]
    for mechanism, needle in (
        (".sect bodies", r"#zenith \.sect-body\{display:block!important\}"),
        ("details.fold", r"#zenith details\.fold::details-content"),
        ("[hidden] receipts", r"#zenith \.drill-body\[hidden\]"),
        ("inactive presentations", r"#zenith \.mode-off\{display:block!important\}"),
    ):
        assert re.search(needle, block), (
            f"{mechanism} has no print override, so that content is absent "
            "from every exported PDF")
