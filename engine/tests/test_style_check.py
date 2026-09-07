"""'The AI right now is saying I believe this is what is happening, and we
don't know. That's not how it works in this industry.'

A prompt instruction cannot guarantee compliance. This flags what got
through anyway — never rewrites, since silently editing a model's claim is
its own kind of hallucination risk.
"""

from engine.reports import style_check


def test_first_person_is_flagged():
    hits = style_check.check("I believe the record shows contested claims.")
    assert any(h["kind"] == "first_person" for h in hits)


def test_a_hedge_word_is_flagged():
    hits = style_check.check("Perhaps the immunity claim was decisive.")
    assert any(h["kind"] == "hedge" for h in hits)


def test_a_plain_declarative_statement_is_never_flagged():
    hits = style_check.check(
        "The court struck the IMF out of the case and granted it immunity.")
    assert hits == []


def test_thin_evidence_stated_plainly_is_not_a_hedge():
    """The instruction is: state uncertainty plainly, don't hedge around it.
    'The record does not establish X' is the correct form and must never be
    flagged as a violation of the rule it is following."""
    hits = style_check.check("The record does not establish who initiated the claim.")
    assert hits == []


def test_annotate_flags_only_the_fields_that_violate():
    analysis = {"verdict": "I believe this is contested.",
               "involvement": "The senator filed the petition."}
    out = style_check.annotate(analysis)
    assert "verdict" in out["style_flags"]
    assert "involvement" not in out["style_flags"]


def test_annotate_adds_nothing_when_everything_is_clean():
    analysis = {"verdict": "The court granted immunity."}
    out = style_check.annotate(analysis)
    assert "style_flags" not in out


def test_annotate_never_mutates_the_input():
    analysis = {"verdict": "I believe this."}
    original = dict(analysis)
    style_check.annotate(analysis)
    assert analysis == original


def test_annotate_handles_empty_input():
    assert style_check.annotate({}) == {}
    assert style_check.annotate(None) is None
