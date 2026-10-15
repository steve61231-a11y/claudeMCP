"""The page told an operator another run would not help, and it would.

A finished run showed:

    Sections this run did not produce
    The run has finished. These were not produced — a first report has no
    earlier one to compare against, and some sections need data this
    subject does not have yet. Waiting will not add them; another run
    might.

        Sentiment over time        Sentiment Framework V1.0

One sentence, printed over every missing section regardless of why.

For "Sentiment over time" it is true: a first report has no history to
plot, and no amount of running fixes that. For "Sentiment Framework" it was
false. That section is gated on `out_of_time("sentiment_framework")`, so it
had been SKIPPED BECAUSE THE RUN HIT ITS CEILING — and the stage ledger
recorded exactly that, in words, while `finishedSections` threw the ledger
away and rendered a blanket excuse over the top of it.

The founding rule broken twice in one card: a section skipped for time made
to look like a section with nothing to say, and then the remedy stated
backwards — "waiting will not add them" about the one section that another
run very probably would add.

The ledger knows. The page has to read it.
"""

from pathlib import Path

PAGE = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
BLOCK = PAGE[PAGE.index("function missingReason"):PAGE.index("function renderReport")]


def test_the_blanket_excuse_is_gone():
    assert "Waiting will not add them" not in PAGE, (
        "one sentence is still printed over every missing section, whatever "
        "the ledger says about each")


def test_the_ledger_is_actually_consulted():
    """It was present in the payload as `sectionStatus` and never read by
    this card."""
    assert "finishedSections(meta.sections_ready, r.sectionStatus)" in PAGE, (
        "the card is still built without the ledger, so it cannot know why "
        "anything is missing")
    assert "ledger.stages" in BLOCK


def test_a_stage_skipped_for_time_says_so_and_says_to_retry():
    """The live case. `out_of_time` records "skipped: the run passed its
    1800s ceiling", and another run with more headroom should produce it."""
    assert "ceiling" in BLOCK, "the ceiling wording the ledger uses is not matched"
    assert "another run should produce it" in BLOCK


def test_a_first_report_section_is_not_told_to_try_again():
    """The opposite error, and just as bad: telling a reader to re-run for
    something that structurally cannot exist until there IS an earlier run
    is a loop with no exit."""
    assert "COMPARISON_SECTIONS" in PAGE
    assert "needs an earlier report to compare against" in BLOCK


def test_a_failed_stage_shows_its_actual_error():
    """"The stage failed" with no reason is the thing the stage ledger was
    built to replace."""
    assert "the stage failed: ${err}" in BLOCK


def test_a_section_with_no_record_does_not_invent_one():
    """Four known reasons and an honest fifth. Guessing the common case is
    what the grounding banner did, and it cost an evening."""
    assert "no reason was recorded for this one" in BLOCK


def test_the_headline_changes_with_what_is_actually_missing():
    """A card whose summary says "none of them will change by waiting"
    above a section that ran out of time is the original bug in a shorter
    sentence."""
    assert "worth another run" in BLOCK
    assert "none of them will change by waiting" in BLOCK


def test_the_list_is_appended_once():
    """It was appended twice. Harmless in the DOM — the second call moves
    the same node — but it is a copy-paste that survived review, and the
    next edit between the two calls would not be."""
    card = BLOCK[BLOCK.index("function finishedSections"):]
    assert card.count("c.appendChild(list)") == 1
