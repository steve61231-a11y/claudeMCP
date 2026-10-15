"""The executive brief opened with a timestamp column.

"During the 2026-09-15 to 2026-10-15 window, sentiment toward W…" — the
first sentence of the most-read paragraph in the product, formatted like log
output. It got there honestly: `sections.py` handed the model
`f"{window_start.date()} to {window_end.date()}"` and the model used what it
was given.

Kenya writes dates the British way. A report that formats them the other way
tells a reader it was generated rather than written, which is the opposite
of what this product is selling.

Two fixes, because either alone leaves the hole open: the prompt stops
emitting ISO, and anything that reaches prose anyway is rewritten — a model
quotes dates out of the corpus too, and the corpus is full of them.
"""

from datetime import datetime
from pathlib import Path

import pytest

from engine.reports import dates, prose

ROOT = Path(__file__).resolve().parents[1] / "reports"


def test_the_window_handed_to_the_model_is_not_iso():
    assert dates.window(datetime(2026, 9, 15), datetime(2026, 10, 15)) == \
        "15 September 2026 to 15 October 2026"


def test_the_live_sentence_is_fixed():
    assert dates.britishise(
        "During the 2026-09-15 to 2026-10-15 window, sentiment fell."
    ) == "During the 15 September 2026 to 15 October 2026 window, sentiment fell."


@pytest.mark.parametrize("text", [
    "See https://nation.africa/2026-10-15/story for more.",
    "The filing [ref=2026-10-15] confirms it.",
    "Logged at 2026-10-15T09:30:00Z yesterday.",
    "Serial 2026-99-99 was issued.",
])
def test_what_must_not_be_rewritten(text):
    """A date inside a url or a citation marker is a path segment, not a
    date a reader is reading — rewriting it breaks the link, and a broken
    link in the evidence section is worse than an ISO date in the prose."""
    assert dates.britishise(text) == text


def test_a_short_string_is_a_label_not_prose():
    """Ids, enum values and column headers are not analysis, and an id that
    happens to look like a date must survive intact."""
    payload = {"label": "2026-10-15",
               "brief": "A filing dated 2026-10-03 was lodged at the registry."}
    prose.map_prose(payload, dates.britishise)
    assert payload["label"] == "2026-10-15"
    assert "3 October 2026" in payload["brief"]


def test_it_reaches_prose_at_any_depth():
    """Named field lists are how the one page that needed this misses it —
    the lesson citations.py records."""
    payload = {"a": {"b": [{"c": "The hearing on 2026-10-03 was adjourned "
                                 "until further notice."}]}}
    prose.map_prose(payload, dates.britishise)
    assert "3 October 2026" in payload["a"]["b"][0]["c"]


def test_both_products_apply_it():
    """'Also goes for the issue map as well.'"""
    for name in ("sections.py", "issue_map.py"):
        body = (ROOT / name).read_text()
        assert "dates.britishise" in body, f"{name} can still ship ISO prose"


def test_no_prompt_formats_its_own_window():
    """A second analyst added later must not quietly reintroduce the ISO
    form by formatting its own window — which is exactly how this one got
    in."""
    for name in ("sections.py", "issue_map.py", "analysts.py", "digest.py"):
        body = (ROOT / name).read_text()
        assert "window_start.date()} to {window_end.date()" not in body, (
            f"{name} formats the window itself instead of using dates.window()")
