"""The most citable document in the corpus rendered as the least.

Kenya Gazette notices and court judgments now reach the corpus. Nothing on
the page could tell one from a tweet, and the evidence section could not
show one at all.

Two separate faults, and the second is worse than the cosmetic one:

  **It was structurally excluded.** "Representative mentions" ranked by
  engagement — views plus likes×5 — and the state does not post for
  engagement. A Gazette notice scores zero, so it sorted last and was cut
  from the top ten every time. The primary record was collected, stored,
  and then kept out of the one section a client opens to check the work.

  **It was unmarked.** The entire argument for collecting the state's own
  publications is that a reader can tell "a newspaper reported an
  appointment" from "here is the notice that made it". That argument is
  worth nothing if the page renders them identically.

The fix for the first is NOT to invent an engagement score for a gazette
notice — a fabricated number is the kind of thing this codebase removes.
Room is reserved instead, the way `learning.py` reserves a share of every
budget for questions never asked: a guaranteed slot cannot be out-competed
by the category that is always plentiful.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT.parent / "web" / "pulse_app.html").read_text()
SERVER = (ROOT / "api_server.py").read_text()

SAMPLE = 10


def _pick(n_primary, n_rest):
    """The selection as api_server performs it."""
    scored = ([{"id": f"P{i}", "_score": 0, "_primary": True}
               for i in range(n_primary)]
              + [{"id": f"N{i}", "_score": 1000 - i, "_primary": False}
                 for i in range(n_rest)])
    scored.sort(key=lambda m: m["_score"], reverse=True)
    primary = [m for m in scored if m["_primary"]]
    rest = [m for m in scored if not m["_primary"]]
    reserved = min(len(primary), SAMPLE // 2)
    chosen = primary[:reserved] + rest[:SAMPLE - reserved]
    chosen.sort(key=lambda m: (m["_primary"], m["_score"]), reverse=True)
    return [m["id"] for m in chosen]


def test_a_gazette_notice_with_no_engagement_still_appears():
    """The live defect. Three notices against twenty news items, and the
    notices score zero on a measure of popularity."""
    picked = _pick(3, 20)
    assert picked[:3] == ["P0", "P1", "P2"], (
        f"the primary record was out-competed by engagement: {picked}")
    assert len(picked) == SAMPLE


def test_the_primary_record_cannot_flood_the_sample():
    """A reader still needs to see what was said about it. Reserving room
    is not the same as taking the lot — the cap is the other half of the
    rule `learning.py` applies to its exploit share."""
    picked = _pick(20, 20)
    assert sum(p.startswith("P") for p in picked) == SAMPLE // 2
    assert sum(p.startswith("N") for p in picked) == SAMPLE // 2


def test_nothing_changes_when_there_is_no_primary_record():
    """Most subjects will have none, and their reports must be unaffected."""
    assert _pick(0, 20) == [f"N{i}" for i in range(SAMPLE)]
    assert _pick(0, 0) == []


def test_a_short_sample_is_not_padded():
    assert _pick(2, 3) == ["P0", "P1", "N0", "N1", "N2"]


def test_the_payload_carries_what_kind_of_document_it_is():
    """The page cannot mark what it was never told."""
    assert '"source_type": mention.source_type' in SERVER
    assert '"record": (mention.raw_payload or {}).get("record")' in SERVER


def test_internal_ranking_fields_do_not_leak_to_the_client():
    """`_score` and `_primary` are how the sample was chosen, not facts
    about a document."""
    assert 'if not k.startswith("_")' in SERVER, (
        "the sample is filtered by an exact key name again, so a new "
        "internal field will ship to the browser")


def test_the_page_marks_it():
    assert "rec-badge" in PAGE, "the primary record renders as an ordinary row"
    assert "is-primary" in PAGE
    assert "Gazette notice" in PAGE and "Judgment" in PAGE, (
        "the badge does not name the kind of document, so a reader learns "
        "only that it is somehow special")


def test_the_badge_is_driven_by_the_payload_not_the_platform():
    """Matching on a platform string would break the moment a second
    source published the same kind of document."""
    block = PAGE[PAGE.index("const RECORD_KIND"):][:400]
    assert "item.record==='primary'" in block.replace(" ", ""), (
        "the badge is not keyed on the record flag the collector sets")
