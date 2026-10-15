"""A spike of 380 mentions on a day nothing happened.

A live report drew "peak 380 mentions in a day" over a 699-item corpus,
with five identical bars on the key-dates timeline. More than half the
corpus on one day is a dramatic finding, and it was an artefact.

Eleven connectors fall back to `window_end` when a date will not parse.
That is the right fallback — it keeps ordering and windowing working — but
it stamps every undated item onto the same day. The volume chart then
counted that pile as real, put the cliff on the last day of every report,
and reported it as the single most striking number on the page.

The founding rule: an unknown must not render as a value. "We could not
date this" had become "this happened on 15 October".

So connectors now say which it is, the flag travels to the analysts, and
day counts are built only from items whose date is known. With nothing
datable there is no peak at all — reporting the window end as the busiest
day would be the same lie in a smaller font.
"""

from datetime import datetime
from pathlib import Path

import pytest

from engine.reports.analysts import narrative_genealogy

ING = Path(__file__).resolve().parents[1] / "ingestion"

LIVE_CONNECTORS = ("gdelt_connector.py", "google_news_rss_connector.py",
                   "youtube_connector.py", "wayback_connector.py",
                   "reddit_connector.py", "kenya_law_connector.py")


def _m(day, estimated=False):
    return {"posted_at": datetime(2026, 10, day), "platform": "news",
            "author_handle": "a", "source_url": "u", "text": "t",
            "date_estimated": estimated}


def test_the_undated_pile_is_not_the_peak():
    """The live defect. Eight undated items stamped on the 15th against a
    real busiest day of three on the 3rd."""
    real = [_m(1), _m(1), _m(2), _m(3), _m(3), _m(3)]
    undated = [_m(15, True) for _ in range(8)]
    origin = narrative_genealogy(real + undated)
    assert origin["peak"]["date"] == "2026-10-03", (
        f"the undated pile won the peak again: {origin['peak']}")
    assert origin["peak"]["mentions"] == 3


def test_the_reading_says_how_much_of_the_corpus_it_rests_on():
    """A peak drawn from six datable items out of fourteen is a different
    claim from one drawn from all of them."""
    origin = narrative_genealogy([_m(1), _m(2), _m(3)]
                                 + [_m(15, True) for _ in range(8)])
    assert origin["dated_mentions"] == 3
    assert origin["undated_mentions"] == 8


def test_nothing_datable_means_no_peak():
    """Not a peak of zero on the window end — no peak. A date there would
    be the same invention in a smaller font."""
    origin = narrative_genealogy([_m(15, True) for _ in range(8)])
    assert origin["peak"]["date"] is None
    assert origin["peak"]["mentions"] == 0


def test_a_fully_dated_corpus_is_unaffected():
    """Most of the work here must change nothing for a corpus that parses
    cleanly."""
    origin = narrative_genealogy([_m(1), _m(2), _m(2), _m(3)])
    assert origin["peak"] == {"date": "2026-10-02", "mentions": 2}
    assert origin["undated_mentions"] == 0


@pytest.mark.parametrize("name", LIVE_CONNECTORS)
def test_every_live_connector_says_whether_it_knew_the_date(name):
    """A fallback that cannot be told apart from a real reading is how this
    got into production. Each connector that can run today must mark it."""
    body = (ING / name).read_text()
    assert "window_end" in body, f"{name} has no date fallback to mark"
    assert "date_estimated" in body, (
        f"{name} falls back to the window end without saying so, so its "
        "undated items will be counted as a real day again")


def test_the_flag_reaches_the_analysts():
    """raw_payload is not carried into the analyst mention dicts, so the
    flag has to travel explicitly or the consumer never sees it."""
    pipeline = (Path(__file__).resolve().parents[1] / "pipeline.py").read_text()
    assert '"date_estimated"' in pipeline, (
        "the flag is set by connectors and dropped before anything can "
        "read it")


def test_the_page_does_not_render_a_peak_that_does_not_exist():
    """`peak` is an object even when its date is None, so a truthiness
    check on it alone printed "peaked —"."""
    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    assert "o.peak&&o.peak.date" in page
    assert "origin.peak&&origin.peak.date" in page
    assert "could not be dated and are not in this reading" in page, (
        "the undated count is not disclosed, so a peak resting on a tenth "
        "of the corpus reads like one resting on all of it")
