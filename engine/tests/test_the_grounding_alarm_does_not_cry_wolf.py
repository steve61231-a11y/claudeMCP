"""The banner told an operator not to send a clean report to a client.

A live run ended with:

    15 names in this report do not appear in any source.
    Checked 300 names against the collected corpus. A name the sources do
    not contain was not reported — it was invented.

Not one of the fifteen was an invented name:

    0e01efc4-cdc0-4200-aed1-59abce9b926a   a mention id
    7b751016-3539-4a86-966c-ef9bfa7fb6b5   a mention id
    HTTPError                              an exception class
    SocialCrawl                            one of our own connectors
    Videos, Recognition                    sentence-case common nouns
    Dominant Narratives and                a cut heading
    Kenya Kwanza's                         the governing coalition

A UUID tokenises to `abce` — four letters, long enough to count as
distinctive, present in no corpus ever written. `Kenya Kwanza's` tokenised
with the possessive attached, so it could not match the "Kenya Kwanza" the
sources say constantly. The rest are this engine's own vocabulary and page
furniture reaching prose through diagnostic fields.

This file's own comments already named the hazard — "a banner that cries
wolf is a banner an operator learns to ignore, which loses the real case" —
and then the entity model handed it candidates the capitalised-pair
fallback was designed to exclude.

The rule, which is the founding rule pointed at this check: a name that
cannot be judged must be reported as UNCHECKED, never as invented.
"""

import pytest

from engine.reports import grounding

CORPUS = ("the kenya kwanza coalition met on tuesday. musalia mudavadi "
          "spoke at the treasury about the budget.")


@pytest.mark.parametrize("name,reason", [
    ("0e01efc4-cdc0-4200-aed1-59abce9b926a", "machine identifier"),
    ("7b751016-3539-4a86-966c-ef9bfa7fb6b5", "machine identifier"),
    ("HTTPError", "code identifier"),
    ("SocialCrawl", "code identifier"),
    ("Dominant Narratives and", "sentence fragment"),
])
def test_each_false_positive_is_no_longer_judged(name, reason):
    ok, why = grounding.is_checkable(name)
    assert not ok, f"{name!r} is still being judged as a name"
    assert why == reason


@pytest.mark.parametrize("word", ["Videos", "Recognition", "Tuesday"])
def test_a_lone_capitalised_word_is_checked_but_never_accused(word):
    """The asymmetry, because the two errors are not equal.

    A single word IS worth checking — "Treasury", "Parliament" and
    "Mudavadi" are real, single-word, and dropping them would make the
    entity model no more thorough than the pattern it is recommended over.
    But a single word the corpus lacks could be an invented person or it
    could be sentence case, and accusing a report of fabricating
    "Recognition" is how an operator learns to ignore the one banner that
    matters."""
    assert grounding.is_checkable(word)[0], f"{word!r} is not even checked"
    assert not grounding.is_accusable(word), f"{word!r} can still be accused"


def test_a_real_single_word_organisation_is_still_checked():
    assert grounding.is_checkable("Treasury")[0]
    assert grounding.is_grounded("Treasury", CORPUS)


def test_the_governing_coalition_is_not_an_invention():
    """A possessive is not part of the name. This one accused the report of
    fabricating Kenya Kwanza."""
    assert grounding.is_checkable("Kenya Kwanza's")[0]
    assert grounding.is_grounded("Kenya Kwanza's", CORPUS), (
        "the possessive still blocks the match")


def test_a_real_invention_is_still_caught():
    """The whole point of the check survives. Raising the bar must not
    disarm it."""
    assert grounding.is_checkable("Peter Kamau")[0]
    assert not grounding.is_grounded("Peter Kamau", CORPUS)


def test_a_real_name_in_the_corpus_passes():
    assert grounding.is_grounded("Musalia Mudavadi", CORPUS)
    assert grounding.is_grounded("Mudavadi", CORPUS)


def test_the_capitalised_fallback_never_produced_these_anyway(monkeypatch):
    """Worth stating, because it locates the fault.

    The two-capitalised-words fallback could not yield `HTTPError`, a UUID
    or `Videos` — it requires two capitalised words, by design, for exactly
    this reason. The candidates came from the entity model, which runs
    whenever LOW_MEMORY is false. So the extractor that was always safe is
    not the one that needs the guard; the guard has to sit after BOTH."""
    payload = {"brief": "The report discusses HTTPError and "
                        "0e01efc4-cdc0-4200-aed1-59abce9b926a at length, "
                        "alongside Musalia Mudavadi."}
    result = grounding.check(payload, [{"content": CORPUS}])
    assert result["method"] == "capitalised-names"
    assert result["ungrounded"] == []


def test_what_the_entity_model_hands_over_is_set_aside_and_counted(monkeypatch):
    """The live path. NER labelled all of these as entities; the guard has
    to catch them after extraction, and say how many it set aside — "we
    could not check this" must not vanish any more than it may be reported
    as an invention."""
    junk = {
        "a": "0e01efc4-cdc0-4200-aed1-59abce9b926a",
        "b": "HTTPError",
        "c": "SocialCrawl",
        "d": "Videos",
        "e": "Dominant Narratives and",
        "f": "Kenya Kwanza's",      # checkable AND grounded
        "g": "Peter Kamau",         # checkable and genuinely absent
    }
    monkeypatch.setattr(grounding, "_names_in",
                        lambda strings: (junk, "entity-model", ""))

    result = grounding.check({"brief": "x" * 60}, [{"content": CORPUS}])

    assert result["ungrounded"] == ["Peter Kamau"], (
        f"the alarm still names things that are not names: "
        f"{result['ungrounded']}")
    assert result["not_checkable"] == 4, (
        "a UUID, two code identifiers and a fragment should be set aside")
    assert set(result["not_checkable_reasons"]) == {
        "machine identifier", "code identifier", "sentence fragment"}
    # "Videos" is checked and found absent, but reported as unverified
    # rather than as an invention.
    assert result["unverified_single_words"] == ["Videos"]
