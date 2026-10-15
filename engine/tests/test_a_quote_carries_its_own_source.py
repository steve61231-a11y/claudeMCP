"""Five headlines in a row, each saying "source not traced".

They were in the section a client reads to check the work, under a
storyline about presidential appointments, and every one of them had been
proved traceable minutes earlier.

`_validate_quotes` keeps a quote only when its ref resolves to a real
mention AND the quoted text is genuinely in that mention. It then copies
that mention's url onto the quote. So by the time a quote exists at all,
its source is known.

Two separate faults threw that away:

  1. `_validate_quotes` read `source_url` alone. GDELT — the news backbone,
     and most of this corpus — stores an article's address in
     `raw_payload["url"]` and leaves `source_url` empty, which
     `build_ref_index` already knew and this did not. Two readers of the
     same fact read it two different ways.
  2. The page's `sourceLine()` ignored the quote's own fields and looked
     the ref up in the index instead — preferring a pointer over the thing
     the pointer had already resolved to.

Either one alone produces "source not traced" on a quote whose source is
sitting in the object being rendered.
"""

from pathlib import Path

from engine.reports.analysts import _validate_quotes

ROOT = Path(__file__).resolve().parents[2]


def _mention(**over):
    base = {
        "id": "abcd1234efgh",
        "text": "Ruto, CSs make fresh appointments to state agencies today.",
        "author_handle": "@standardkenya",
        "platform": "news",
        "source_type": "article",
        "source_url": None,
        "raw_payload": {"url": "https://standardmedia.co.ke/appointments"},
        "posted_at": "2026-10-02",
    }
    base.update(over)
    return base


def test_an_article_url_in_raw_payload_reaches_the_quote():
    """The live case. GDELT leaves source_url empty."""
    src = _mention()
    [quote] = _validate_quotes(
        [{"ref": "abcd1234", "text": "Ruto, CSs make fresh appointments"}],
        {"abcd1234": src})
    assert quote["url"] == "https://standardmedia.co.ke/appointments", (
        "the article's address was in raw_payload and was not read, so the "
        "page has a quote it cannot link")


def test_source_url_still_wins_when_there_is_no_raw_payload():
    src = _mention(raw_payload=None, source_url="https://nation.africa/story")
    [quote] = _validate_quotes(
        [{"ref": "abcd1234", "text": "Ruto, CSs make fresh appointments"}],
        {"abcd1234": src})
    assert quote["url"] == "https://nation.africa/story"


def test_the_quote_carries_its_date_too():
    """Without it the attribution line can name an outlet and no date, and
    an undated quote in an intelligence report is worth noticeably less."""
    [quote] = _validate_quotes(
        [{"ref": "abcd1234", "text": "Ruto, CSs make fresh appointments"}],
        {"abcd1234": _mention()})
    assert quote.get("posted_at") == "2026-10-02"


def test_the_page_prefers_the_quote_over_the_index():
    """A quote that already carries its source must not be re-looked-up and
    found missing."""
    page = (ROOT / "web" / "pulse_app.html").read_text()
    body = page.split("function sourceLine")[1].split("\n  }")[0]
    assert "own" in body, "sourceLine still reads only the ref index"
    assert "REF_INDEX[ref]" in body, "the index is still the fallback"
    # And an absent field on the quote must not clobber a good one.
    assert "if(own && own[k])" in body, (
        "an undefined url on the quote would overwrite the index's, which "
        "is a second way to lose the link")


def test_every_call_site_passes_the_quote():
    page = (ROOT / "web" / "pulse_app.html").read_text()
    calls = [line for line in page.splitlines() if "sourceLine(" in line
             and "function sourceLine" not in line]
    assert calls, "no call sites found — this test has stopped checking anything"
    for line in calls:
        assert line.rstrip().count(",") >= 1, (
            f"a call site does not hand sourceLine the quote: {line.strip()[:90]}")
