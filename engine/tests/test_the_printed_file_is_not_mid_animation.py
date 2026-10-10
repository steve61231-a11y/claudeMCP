"""The exported PDF printed a number that was wrong.

The sentiment ring counted its own figure up from zero over 1.1 seconds. A
page captured before the count finished printed whatever frame it had
reached — an exported file read **44.4%** where the report said **47.3%**.
Bars that grew from zero width printed empty, and timeline entries that
faded in printed blank.

Both halves of that are this product's forbidden failure. A deliverable
whose numbers depend on how long the reader happened to wait is not a
deliverable; and a chart that is still growing and a chart with nothing in
it are precisely the two things everything else here works to keep apart.

The rule: **the page is correct and complete in its first painted frame.**
Animation only moves it from correct to correct. `animate()` therefore
calls `step(1)` synchronously before it requests a frame, and snaps
anything still running when a print begins.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.api_server import render_frontend_document

PAGE = render_frontend_document()


def _animate_body() -> str:
    start = PAGE.index("  function animate(ms,step){")
    return PAGE[start: PAGE.index("\n  }", start)]


def test_animate_reaches_its_final_state_before_it_asks_for_a_frame():
    body = _animate_body()
    final = body.index("step(1)")
    frame = body.index("requestAnimationFrame")
    assert final < frame, (
        "animate() starts animating before it sets the final value, so a "
        "capture in the first frame shows a number that is not the report's")


def test_a_print_snaps_whatever_is_still_moving():
    """For the capture that lands mid-animation rather than before it."""
    assert "snapAnimations" in PAGE
    assert "beforeprint" in PAGE, "a print begun mid-animation is not caught"
    assert "matchMedia('print')" in PAGE, (
        "Chromium's print-to-pdf path does not always fire beforeprint")


def test_nothing_reveals_itself_on_a_timer():
    """The pattern that caused this: paint it empty, fill it in later.

    A `setTimeout` that sets a width or an opacity leaves the element
    EMPTY in the DOM until the timer fires, so there is no final state to
    fall back on — which is why this is a rule about the mechanism and not
    a list of the four places it had gone wrong."""
    offenders = [
        line.strip()
        for line in PAGE.splitlines()
        if "setTimeout(" in line
        and re.search(r"style\.(width|opacity|height|transform)", line)
    ]
    assert not offenders, (
        "these reveal themselves on a timer, so a capture before it fires "
        "prints them empty: " + " | ".join(offenders))


# --- the outcome, not the mechanism -----------------------------------------

CHROME = None
try:
    from engine.reports import pdf_export
    CHROME = pdf_export.chrome_path()
except Exception:
    pass

requires_chrome = pytest.mark.skipif(CHROME is None, reason="no Chromium here")


def _print_demo(tmp_path: Path, budget: int) -> str:
    boot = ("<script>window.addEventListener('load', function(){"
            " window.ZENITH.renderReport(document.getElementById('view'),"
            " window.ZENITH.DEMO.report, {live:false}); });</script>")
    cut = PAGE.rfind("</body>")
    src = tmp_path / f"r{budget}.html"
    src.write_text(PAGE[:cut] + boot + PAGE[cut:], encoding="utf-8")
    out = tmp_path / f"r{budget}.pdf"
    subprocess.run(
        [str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--no-first-run", "--hide-scrollbars",
         f"--virtual-time-budget={budget}", f"--print-to-pdf={out}",
         "--no-pdf-header-footer", str(src)],
        capture_output=True, timeout=180)
    return subprocess.run(["pdftotext", "-layout", str(out), "-"],
                          capture_output=True, text=True).stdout


@requires_chrome
@pytest.mark.parametrize("budget", [900, 9000])
def test_the_printed_score_is_the_reported_score_however_early_it_is_captured(
        tmp_path, budget):
    """900ms is inside the ring's old 1.1s count-up; 9s is well past it.

    Both must print the same figure, and it must be the one in the report.
    """
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")
    declared = re.search(r"sentiment_score:\{score:([\d.]+)", PAGE).group(1)
    text = _print_demo(tmp_path, budget)
    assert f"{declared}%" in text, (
        f"the printed file does not contain the reported score {declared}% "
        f"at a {budget}ms capture — it is printing a frame of an animation")
