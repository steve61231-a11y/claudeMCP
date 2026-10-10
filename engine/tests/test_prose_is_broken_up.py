"""The summary was a wall.

Section 1.0 of the client deliverable rendered the analyst's executive
summary as one block of `white-space:pre-wrap`: nine sentences, no
paragraph breaks, well over a thousand characters, set in the muted grey
this page uses for captions. The client's note on it was three words —
"break up this section" — and they were right for a reason worth writing
down. That paragraph is what a Cabinet Secretary reads standing up, and the
page was asking them to find the point in it themselves.

What `prose()` does, and the order it matters in:

- **The opening is promoted.** An analyst's first sentence is the finding
  and the rest is how they got there. Setting both at caption size told the
  reader nothing about which was which.
- **A paragraph that is really nine sentences is split.** Models often emit
  no blank lines at all, so honouring the breaks a text happens to contain
  means honouring none of them.
- **The tail folds**, and prints open like every other fold here.

What it must never do is change the words. Every sentence the model
produced reaches the page; this decides where they go, not which ones
survive. The test for that is below, and it is the one that matters.
"""

import json
import re
import shutil
import subprocess

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

#: Nine sentences, no blank line anywhere — the production shape.
WALL = (
    "The window divides cleanly between two reforms. "
    "Taxation led the conversation for two periods and has since receded. "
    "Health financing has taken its place and is still climbing. "
    "The two are not read separately by the public at all. "
    "A levy of 2.75% is deducted reliably every month. "
    "The service it buys is described across counties as unreliable. "
    "That lets a service complaint be voiced as a tax complaint. "
    "Sentiment is net-negative but it is also shallow. "
    "The ambivalent middle is larger than either camp."
)


def _in_page(expr: str):
    """Evaluate an expression against the real page, in a real browser."""
    boot = ("<script>window.addEventListener('load',function(){"
            "document.title=JSON.stringify(" + expr + ");});</script>")
    cut = PAGE.rfind("</body>")
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "p.html"
        src.write_text(PAGE[:cut] + boot + PAGE[cut:], encoding="utf-8")
        dom = subprocess.run(
            [str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-dev-shm-usage", "--no-first-run",
             "--virtual-time-budget=6000", "--dump-dom", str(src)],
            capture_output=True, text=True, timeout=180).stdout
    return json.loads(re.search(r"<title>(.*?)</title>", dom, re.S).group(1))


@requires_chrome
def test_not_one_word_of_the_prose_is_lost():
    """The assertion that matters. Breaking a paragraph up is a decision
    about where sentences go, never about which ones survive — a summary
    quietly shortened is indistinguishable from a model that wrote less."""
    # The paragraph nodes, including the ones inside the fold — the fold's
    # own summary is a label this adds, not prose the model wrote, so it is
    # not part of the comparison. Joined with a space because that is what
    # separate block elements render as.
    paras = _in_page(
        "[...window.ZENITH.prose(" + json.dumps(WALL) + ")"
        ".querySelectorAll('.prose-lead,.prose-p')].map(n=>n.textContent)")
    normalise = lambda s: " ".join(s.split())
    assert normalise(" ".join(paras)) == normalise(WALL), (
        "the rendered prose is not the prose that went in")


@requires_chrome
def test_a_wall_of_nine_sentences_becomes_paragraphs():
    counts = _in_page(
        "(function(){var b=window.ZENITH.prose(" + json.dumps(WALL) + ");"
        "return {leads:b.querySelectorAll('.prose-lead').length,"
        " paras:b.querySelectorAll('.prose-p').length,"
        " folds:b.querySelectorAll('details.fold').length};})()")
    assert counts["leads"] == 1, "the finding is not set apart from the working"
    assert counts["paras"] >= 2, f"nine sentences stayed one block: {counts}"
    assert counts["folds"] == 1, "the tail is not folded away"


@requires_chrome
def test_a_short_summary_is_left_alone():
    """Three sentences is not a wall, and folding it would hide a third of
    a short answer behind a chevron for nothing."""
    short = ("Coverage is dominated by health financing. "
             "Official messaging frames it as reform. "
             "Reaction is cost-of-living framed.")
    counts = _in_page(
        "(function(){var b=window.ZENITH.prose(" + json.dumps(short) + ");"
        "return {leads:b.querySelectorAll('.prose-lead').length,"
        " paras:b.querySelectorAll('.prose-p').length,"
        " folds:b.querySelectorAll('details.fold').length};})()")
    assert counts == {"leads": 1, "paras": 0, "folds": 0}, counts


@requires_chrome
def test_a_decimal_is_not_the_end_of_a_sentence():
    """"Sh4.8 trillion" and "nation.africa" are not sentence boundaries,
    and splitting on the full stop alone produced three-word paragraphs."""
    text = ("The budget is Sh4.8 trillion in total. "
            "It was reported first by nation.africa on the day. "
            "The figure includes KSh 9.4 billion for settlement. "
            "No source disputes it. "
            "Parliament has not yet voted. "
            "The vote is expected within weeks. "
            "That timetable is itself contested now. "
            "Two committees have asked for more time. "
            "Neither has published a reason for the request.")
    paras = _in_page(
        "[...window.ZENITH.prose(" + json.dumps(text) + ")"
        ".querySelectorAll('.prose-lead,.prose-p')].map(n=>n.textContent)")
    for para in paras:
        assert len(para.split()) > 4, f"split mid-number: {para!r}"
    assert "Sh4.8 trillion in total." in " ".join(paras)


def test_no_wall_of_text_is_rendered_by_hand_any_more():
    """The four sites that did this each did it slightly differently, which
    is why this is a rule about the mechanism rather than four fixes.

    `white-space:pre-wrap` on a long analyst field is the signature: it
    honours whatever line breaks the model happened to emit, which for most
    models is none, and sets the whole thing at one size.
    """
    long_fields = ("executive_summary", "executiveBrief", "involvement",
                   "tension_or_risk", "bg.outline")
    offenders = [
        line.strip() for line in PAGE.splitlines()
        if "pre-wrap" in line and any(f in line for f in long_fields)
    ]
    assert not offenders, (
        "long analyst prose is still being dumped as one block: "
        + " | ".join(offenders))


def test_prose_is_exported_so_it_can_be_tested_and_reused():
    assert re.search(r"window\.ZENITH\s*=\s*\{[^}]*\bprose\b", PAGE), (
        "prose() is not on the ZENITH export, so the PDF path and these "
        "tests cannot reach it")


@requires_chrome
@pytest.mark.parametrize("text", [
    "A levy of 2.75% is deducted monthly. The service is unreliable.",
    "It was reported by nation.africa first. Nobody else carried it.",
    "See No. 3 on the schedule. It is the only clause that binds.",
    'The clinic said "SHA is not working today." He paid cash.',
    "Is it a tax or a benefit?! Nobody in the corpus answers that.",
    "Sh4.8 trillion in total. KSh 9.4 billion of it for settlement.",
])
def test_splitting_never_loses_a_character(text):
    """The property, not the examples. A split cannot lose anything by
    construction — the pieces are the whole string — and this is the test
    that a future "improvement" back to a matching regex has to pass."""
    pieces = _in_page("window.ZENITH.splitSentences(" + json.dumps(text) + ")")
    assert " ".join(" ".join(p.split()) for p in pieces) == " ".join(text.split())
