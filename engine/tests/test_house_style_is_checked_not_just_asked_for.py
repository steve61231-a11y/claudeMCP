"""House style, enforced where it was only requested.

Three problems, all the same shape — something the prompt asks for that
nothing verifies.

**Nothing checked the Search report.** `style_check.annotate` defaulted to
`fields=("involvement", "tension_or_risk", "verdict")`, three issue-map keys.
The executive brief — the single most-read paragraph in the product — had
never been looked at, nor had the insights, the narratives or the storylines.
A hand-written list of field names is the mistake that let raw citations leak
for three rounds here, and it fails the same way: it falls behind the prompts
that produce the fields, and can only be corrected after someone has read the
bad output.

**The word list missed the commonest forms.** "utilise" was in the table and
"utilised" was not, and `\\b` stops the former matching the latter.

**Accusation was indistinguishable from attribution.** The client's own
example: you can write "Steve is a thief", or "the evidence strongly suggests
Steve was involved in...". The first is a statement of fact the file cannot
support and the publisher owns. A due-diligence report about named living
people may report any allegation; it may not make one.

That last check is biased toward flagging on purpose. A properly attributed
sentence that gets flagged costs an editor five seconds. An unattributed
allegation that does not get flagged gets published.
"""

import pytest

from engine.reports import style_check


# --- plain words ---------------------------------------------------------------

@pytest.mark.parametrize("text,expect", [
    ("The subject utilised shell companies.", "use"),
    ("Proceedings commenced in March.", "begin"),
    ("Prior to the audit, records were incomplete.", "before"),
    ("A significant number of contracts followed.", "many"),
    ("The filing is indicative of a pattern.", "shows"),
    ("The majority of coverage was neutral.", "most"),
])
def test_an_inflated_word_is_flagged_with_its_plain_replacement(text, expect):
    """Flagging a word without offering the replacement produces an editor's
    complaint rather than an edit."""
    hits = [h for h in style_check.check(text) if h["kind"] == "inflated_word"]
    assert hits, f"nothing flagged in {text!r}"
    assert any(expect in h.get("fix", "") for h in hits), \
        f"{text!r} flagged but no plain replacement offered: {hits}"


def test_inflected_forms_are_caught_not_just_the_dictionary_form():
    """The table lists "utilise"; prose says "utilised". A word-boundary match
    on the dictionary form alone lets the commonest written form straight
    through a check that appears to cover it."""
    found = {h["phrase"].lower() for h in style_check.check(
        "He utilised the account, commenced proceedings and demonstrated intent.")}
    assert {"utilised", "commenced", "demonstrated"} <= found


def test_padding_is_told_to_be_cut_not_replaced():
    hits = style_check.check("It is important to note that coverage rose.")
    assert any(h.get("fix") == "cut it" for h in hits)


def test_plain_prose_is_left_alone():
    """The check has to be quiet on good writing or nobody will read it."""
    assert style_check.check(
        "The ministry awarded the contract in March. Coverage was heavy.") == []


# --- accusation vs attribution --------------------------------------------------

@pytest.mark.parametrize("text", [
    "Steve is a thief.",
    "Prior to the audit, Steve is a thief.",
    "He stole public funds.",
    "The tender was rigged.",
])
def test_a_bare_accusation_is_flagged(text):
    assert style_check.accusations(text), f"not flagged: {text!r}"


@pytest.mark.parametrize("text", [
    "Court filings allege that Steve embezzled public funds.",
    "An audit found that he embezzled funds.",
    "A court ruled that the tender was rigged.",
    "Reports say he was bribed by the contractor.",
    "He denied the bribery claims.",
    "He is accused of corruption.",
    "The Nation reported that officials looted the fund.",
])
def test_a_reported_allegation_is_not_flagged(text):
    """The report must be free to carry any allegation that is attributed.
    A check that flagged these would be telling the analyst to stop doing
    the job."""
    assert not style_check.accusations(text), f"wrongly flagged: {text!r}"


def test_a_legal_noun_alone_is_not_attribution():
    """"Prior to the audit, Steve is a thief" counted as attributed in the
    first version, because "audit" appeared in the sentence. Only a verb of
    reporting or finding says that somebody did the alleging."""
    assert style_check.accusations("Prior to the audit, Steve is a thief.")
    assert not style_check.accusations("The audit found he was corrupt.")


def test_hedging_does_not_count_as_attribution():
    """"Perhaps X is a thief" is weaker AND still accuses. Attribution is the
    opposite move: state it plainly, say what it rests on."""
    assert style_check.accusations("Perhaps Steve is a thief.")


# --- coverage -------------------------------------------------------------------

def test_every_prose_field_is_checked_not_a_remembered_three():
    out = style_check.annotate({
        "executive_brief": "Prior to the audit, coverage rose.",
        "deep_insights": {"insights": [{"reasoning": "He utilised the account."}]},
        "narrative_deep_dives": [{"deep_dive": {"how_it_unfolded": "Steve is a thief."}}],
    })
    flagged = out["style_flags"]
    assert "executive_brief" in flagged
    assert any("reasoning" in k for k in flagged), "nested insight prose not checked"
    assert any("how_it_unfolded" in k for k in flagged), "deep-dive prose not checked"


def test_structural_fields_are_not_mistaken_for_prose():
    """A url or a ref is not writing and must not be marked up as bad writing."""
    out = style_check.annotate({
        "quotes": [{"ref": "abcd1234", "text": "verbatim quote"}],
        "sources": [{"url": "https://example.com/prior-to-the-audit-story"}],
    })
    assert "style_flags" not in out


def test_the_search_report_is_style_checked_at_all():
    import inspect

    from engine.reports import sections

    assert "style_check.annotate" in inspect.getsource(sections), (
        "the Search report is still never style-checked")


def test_annotate_does_not_mutate_its_input():
    payload = {"executive_brief": "He utilised the account."}
    before = dict(payload)
    style_check.annotate(payload)
    assert payload == before


# --- what the model is told -----------------------------------------------------

def test_the_prompt_asks_for_plain_words_and_attribution():
    """The checks above catch what slips through. The prompt is still the
    first line of defence, and it has to actually say these things."""
    from engine.reports.analysts import GROUNDING_RULES

    assert "PLAIN WORDS" in GROUNDING_RULES
    assert "utilise" in GROUNDING_RULES
    assert "ATTRIBUTION" in GROUNDING_RULES
    assert "may not MAKE one" in GROUNDING_RULES
    assert "ACTIVE VOICE" in GROUNDING_RULES
