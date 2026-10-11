"""The style guide, applied — and the half of it this product must not apply.

The client asked for the Economist style guide from the start ("you must
download that shit"). It is now in the repository, and `style_check` carries
its prescriptive lists rather than my recollection of them: the "short
words" table, the preferences in the introduction, "unnecessary words",
"horrible words", "sloppy writing", and the journalese page.

That last one is the most relevant page in the book for this product,
because it describes the exact voice a model reaches for when asked to
write about politics:

    "Lazy journalists are always at home in oil-rich country A, ruled by
    ailing President B, the long-serving strongman, who is, according to
    the chattering classes, not squeaky clean but a wily political operator
    — hence the present uneasy peace — but, after his recent watershed (or
    ground-breaking or landmark or sea-change) decision to arrest his prime
    minister (the honeymoon is over), will soon face a bloody uprising in
    the breakaway south."

A report on Kenyan politics written in that register is unsellable.

**The harder half is what was left out.** Five of the guide's own pairs are
deliberately not applied, because the replacement is wrong in the
collocations this product actually writes — "health facility" is the
standard Kenyan term and the commonest noun in an SHA report, a UK peer is
a person, firms exit markets, nobody "breaks" human rights, and "the
following three issues" is correct English. `stakeholder` and `governance`
are on the guide's horrible-words list and are also the name of a section
and a framework here.

A check that fires on correct prose is a check its reader learns to skip,
and this codebase has written that down more than once. Applying a style
guide wholesale to a domain it was not written for is how you get one.
"""

import pytest

from engine.reports import style_check


def _kinds(text):
    return {(h["kind"], h["phrase"].lower()) for h in style_check.check(text)}


# --- the guide's lists, applied ----------------------------------------------

@pytest.mark.parametrize("text,phrase,fix", [
    ("Expenditure rose sharply.", "expenditure", "spending"),
    ("He has a track record of delivery.", "track record", "record"),
    ("Budgets were cut back in June.", "cut back", "cut"),
    ("Three firms were bought up.", "bought up", "bought"),
    ("Cutbacks were announced.", "cutbacks", "cuts"),
    ("Weather conditions delayed it.", "weather conditions", "weather"),
    ("At this moment in time nothing is settled.", "at this moment in time", "now"),
    ("Industrial action is expected.", "industrial action", "a strike"),
    ("Following the audit, two officials resigned.", "following", "after"),
])
def test_the_short_and_unnecessary_word_pairs_are_applied(text, phrase, fix):
    hits = [h for h in style_check.check(text)
            if h["phrase"].lower() == phrase]
    assert hits, f"{phrase!r} was not flagged"
    assert fix in hits[0]["fix"], hits[0]


@pytest.mark.parametrize("word", [
    "guesstimate", "proactive", "prestigious", "savvy", "ongoing",
    "poster child", "chattering classes", "looking to",
])
def test_the_horrible_words_are_flagged(word):
    assert ("horrible_word", word) in _kinds(f"The report is {word} in places.")


@pytest.mark.parametrize("phrase", [
    "green light", "thumbs up", "gravy train", "the likes of",
    "watershed", "ground-breaking", "squeaky clean", "uneasy peace",
    "long-serving strongman", "oil-rich", "honeymoon is over",
])
def test_the_journalese_page_is_enforced(phrase):
    """The register a model defaults to when writing about politics, and the
    one thing that makes a report read like a wire story rather than an
    analyst's file."""
    assert ("journalese", phrase) in _kinds(f"It was a {phrase} moment.")


def test_the_guides_own_parody_is_caught_wholesale():
    """If the check cannot flag the paragraph the guide wrote to mock this
    style, it is not enforcing the page."""
    parody = ("Oil-rich country A, ruled by the long-serving strongman, who "
              "is not squeaky clean but a wily political operator, hence the "
              "present uneasy peace, after his recent watershed decision.")
    kinds = _kinds(parody)
    assert len([k for k in kinds if k[0] == "journalese"]) >= 5, kinds


def test_very_is_questioned_rather_than_banned():
    """"If it occurs in a sentence you have written, try leaving it out and
    see whether the meaning is changed." The fix is the guide's own test,
    not an instruction."""
    hits = [h for h in style_check.check("The omens were very good.")
            if h["phrase"].lower() == "very"]
    assert hits and "leaving it out" in hits[0]["fix"]


def test_skills_usually_means_can():
    """"He has the skills probably means He can.\""""
    assert ("jargon", "leadership skills") in _kinds(
        "She has the leadership skills to deliver it.")


# --- the half that must NOT be applied ---------------------------------------

@pytest.mark.parametrize("correct", [
    # The guide says facility → plant. A health facility is the standard
    # Kenyan term and the commonest noun in an SHA report.
    "Registered members were turned away at a health facility.",
    # following → after, but this is the enumerative sense.
    "The following three issues dominate the window.",
    # peer → colleague, but peer review is peer review.
    "Peer review of the audit is still pending.",
    # exit → way out, but a firm exiting a market is not using a door.
    "Two insurers may exit the market this year.",
    # violate → break, but nobody breaks human rights.
    "The court found the directive violated the Act.",
    # breakaway is a fact about a map, not a stock phrase.
    "The breakaway region reported no cases.",
    # establish → set up, but this is the sentence this engine's OWN house
    # style mandates for thin evidence. Its own prescribed phrasing failing
    # its own new check is the clearest possible sign of a style rule
    # applied past the sense it was written for.
    "The record does not establish who initiated the claim.",
    # On the guide's horrible-words list, and the name of a section here.
    "Stakeholder positions are mapped in section 2.",
    "Corporate governance is the subject of the petition.",
])
def test_correct_prose_in_this_domain_is_not_flagged(correct):
    """Applying a style guide wholesale to a domain it was not written for
    produces a check that fires on good writing — and a check that fires on
    good writing is one nobody reads."""
    assert not style_check.check(correct), (
        f"{correct!r} was flagged: {style_check.check(correct)}")


def test_the_products_own_vocabulary_is_excluded_on_purpose():
    """Left out by decision, not by oversight — so a later reader adding
    them back has to argue with this test rather than with a silent gap."""
    assert "stakeholder" not in style_check.HORRIBLE_WORDS
    assert "governance" not in style_check.HORRIBLE_WORDS
    assert "facility" not in style_check.PLAIN_WORDS
    assert "peer" not in style_check.PLAIN_WORDS
    assert "exit" not in style_check.PLAIN_WORDS
    assert "establish" not in style_check.PLAIN_WORDS


def test_the_guide_is_in_the_repository():
    """"This repo is the only thing that survives." A style guide that lives
    in a chat upload is a style guide the next session cannot read."""
    from pathlib import Path

    guide = Path("docs/economist-style-guide.md")
    assert guide.exists(), "the style guide is not in the repo"
    body = guide.read_text(encoding="utf-8")
    for section in ("short words", "unnecessary words", "horrible words",
                    "journalese"):
        assert section in body.lower(), f"{section} is missing from the extract"
