"""A report on a Kenyan politician came back referring to Jews.

Nothing in the corpus did. The model reached for a conspiracy frame that
exists in its training data and not in the evidence, and wrote it into a
document intended for a paying client.

Every other failure pinned in this directory costs time. This one is
defamatory, and one occurrence ends the product.

The fix is not a list of forbidden words — Kenyan political reporting
legitimately concerns ethnic coalitions and religious communities, and a
report that cannot say "Luo" cannot describe Kenyan politics. It is this
repository's existing grounding rule pointed at identity: **if no source
contains the term, the analyst may not introduce it.**

Both directions are pinned below, because each failure mode is as bad as the
other: inventing an attribution is defamation, and dropping one the sources
made is lying by omission.
"""

import pytest

from engine.reports import protected_terms


def _corpus(*texts):
    return [{"content": t} for t in texts]


def test_an_invented_religious_frame_is_removed():
    """The live failure, in the field it appeared in."""
    payload = {"executive_brief": (
        "Ruto appointed new board members to EPRA. Jewish financiers are "
        "said to be behind the Lamu refinery deal. The Standard called it "
        "patronage.")}
    report = protected_terms.redact(
        payload, _corpus("Ruto, CSs make fresh appointments to EPRA and "
                         "LAPSSET. The Standard reports patronage concerns."))
    assert report["removed"] == 1
    assert "Jewish" not in payload["executive_brief"]
    # The rest of the paragraph survives — a guard that takes the whole
    # field with it has replaced a libel with a blank section.
    assert "EPRA" in payload["executive_brief"]
    assert "patronage" in payload["executive_brief"]


def test_what_the_sources_raised_is_left_alone():
    """The half that keeps the product usable. Kenyan coverage names
    communities constantly; a report that silently dropped one its own
    evidence named would be lying by omission."""
    payload = {"brief": "The Kikuyu vote is consolidating behind the deputy "
                        "president, according to coverage this week."}
    before = payload["brief"]
    report = protected_terms.redact(
        payload, _corpus("Analysts say the Kikuyu vote is shifting toward "
                         "the deputy president."))
    assert not report.get("findings")
    assert payload["brief"] == before, "a grounded community was censored"


def test_nothing_is_cut_when_there_is_no_corpus_to_check_against():
    """With no haystack every term looks ungrounded, and a guard that
    deletes the whole report because collection failed has turned a thin
    run into an empty one."""
    payload = {"brief": "The Kikuyu vote is consolidating behind the deputy "
                        "president this week, say analysts."}
    before = payload["brief"]
    report = protected_terms.scan(payload, [])
    assert report["checked"] is False
    assert "skipped" in report, "a check that did not happen must say so"
    assert payload["brief"] == before


def test_it_does_not_fire_on_words_that_merely_contain_a_term():
    """A check that flags correct prose is one its reader learns to skip —
    the conclusion style_check.py reached the hard way. 'jewel' is not
    'jew' and 'blackout' is not 'black'."""
    payload = {"brief": "The jewel heist and the blackout dominated coverage "
                        "across the county this week, according to reports."}
    before = payload["brief"]
    assert not protected_terms.redact(payload, _corpus("Nation reports."))["findings"]
    assert payload["brief"] == before


@pytest.mark.parametrize("sentence,term", [
    ("The Kikuyu establishment engineered this outcome deliberately.", "kikuyu"),
    ("Muslim voters in Mombasa were decisive in the final count.", "muslim"),
    ("Chinese contractors are quietly steering the whole programme.", "chinese"),
    ("This is explained by tribal arithmetic more than by policy choices.", "tribal"),
])
def test_each_family_is_caught_when_ungrounded(sentence, term):
    """Religion was the live case. Ethnicity is the one that matters most
    in this market, and a guard that caught only the reported instance
    would be the per-instance fix this codebase keeps paying for."""
    payload = {"brief": sentence}
    report = protected_terms.redact(payload, _corpus("Coverage of the vote."))
    assert report["removed"] == 1, f"{term!r} was not caught"
    assert payload["brief"] == ""


def test_a_removal_is_declared_rather_than_silent():
    """The founding rule. A deleted sentence that leaves no trace is
    indistinguishable from a section with nothing to say, so the removal
    carries the sentence, the term and the reason."""
    payload = {"brief": "Jewish money is behind the refinery, allegedly."}
    report = protected_terms.redact(payload, _corpus("Refinery coverage."))
    finding = report["findings"][0]
    assert finding["term"] == "jewish"
    assert finding["family"] == "religion"
    assert "Jewish money" in finding["sentence"], "the cut text is not recoverable"
    assert finding["field"] == "brief"
    assert "removed" in report["note"] and "sources" in report["note"]


def test_every_prose_field_is_walked_not_a_named_list():
    """citations.py records what naming fields one at a time costs: a new
    analyst is added, nobody updates the list, and the page that needed
    checking is the one that is not."""
    payload = {"a": {"b": [{"deep": "Jewish financiers run this county, "
                                    "according to nobody in particular."}]}}
    report = protected_terms.redact(payload, _corpus("County coverage."))
    assert report["removed"] == 1
    assert payload["a"]["b"][0]["deep"] == ""


def test_both_products_run_it():
    """'Also goes for the issue map as well' — the standing instruction on
    every change in this product."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "reports"
    for name in ("sections.py", "issue_map.py"):
        body = (root / name).read_text()
        assert "protected_terms.redact" in body, (
            f"{name} does not run the identity guard")
