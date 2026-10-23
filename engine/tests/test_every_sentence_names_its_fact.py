"""Every sentence in the brief names the finding it rests on, and the finding says it.

Phase 3 of docs/FOUNDATION.md. The brief — the most-read paragraph in the
product — was written by a model over the other writers' output, and nothing
mechanical asked of an ordinary sentence which fact it was. Now it is
written from the verified record and each sentence is checked against the
findings it cites before anyone reads it. Each test pins one way a sentence
could say more than the record does.
"""

import pytest

from engine import llm
from engine.reports import from_facts

SUBJECT = "Jane Wanjiru"

FINDINGS = [
    {"statement": "The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
     "status": "reported", "independent_sources": 3, "first_seen": "2026-10-14",
     "sources": [{"publisher": "Daily Nation", "url": "https://n.example/1", "ref": "aaaa1111",
                  "quote": "summoned the Treasury over the delayed county disbursements"},
                 {"publisher": "The Standard", "url": "https://s.example/1", "ref": "bbbb2222",
                  "quote": "Treasury officials were summoned by the committee"}]},
    {"statement": "Jane Wanjiru said fourteen counties could not pay health workers.",
     "status": "reported", "independent_sources": 1, "first_seen": "2026-10-15",
     "sources": [{"publisher": "Daily Nation", "url": "https://n.example/2", "ref": "cccc3333",
                  "quote": "14 counties unable to pay health workers"}]},
    {"statement": "Opposition senators alleged that the funds were diverted to a private account.",
     "status": "alleged", "independent_sources": 1, "first_seen": "2026-10-16",
     "sources": [{"publisher": "The Star", "url": "https://t.example/1", "ref": "dddd4444",
                  "quote": "alleged that the funds had been diverted to a private account"}]},
]
LEDGER = from_facts.build_ledger(FINDINGS)


def _check(sentence, cited, numbers=None):
    return from_facts.check_sentence(sentence, cited, LEDGER, SUBJECT, numbers)


def test_a_faithful_paraphrase_stands():
    assert _check("The Senate Finance Committee summoned the Treasury over delayed disbursements to counties.",
                  ["F1"]) is None


def test_a_sentence_must_cite_a_finding_that_exists():
    assert "cites no finding" in _check("The Treasury was summoned.", [])
    assert "cites no finding" in _check("The Treasury was summoned.", ["F9"])


def test_a_sentence_about_something_else_is_removed():
    reason = _check("Wanjiru is widely expected to run for governor in 2027 on a reform ticket.", ["F2"])
    assert reason and reason.startswith("says more than")


def test_a_figure_the_findings_do_not_state_is_removed():
    assert "figure" in _check("Wanjiru said 23 counties could not pay health workers.", ["F2"])
    assert _check("Wanjiru said 14 counties could not pay health workers.", ["F2"]) is None


def test_a_name_the_findings_do_not_contain_is_removed():
    reason = _check("Treasury Secretary John Mbadi was summoned over delayed county disbursements.", ["F1"])
    assert reason and "John Mbadi" in reason


def test_the_subject_may_be_named():
    assert _check("Jane Wanjiru said fourteen counties could not pay health workers.", ["F2"]) is None


def test_an_allegation_may_not_be_stated_as_established():
    assert "only allege" in _check("The funds were diverted to a private account.", ["F3"])
    assert _check("Opposition senators alleged that the funds were diverted to a private account.",
                  ["F3"]) is None


def test_the_brief_cites_the_sources_behind_each_finding():
    kept = [{"text": "The committee summoned the Treasury over delayed county disbursements.", "facts": ["F1"]},
            {"text": "Wanjiru said fourteen counties could not pay health workers.", "facts": ["F2"]}]
    text, citations = from_facts.render(kept, LEDGER)
    assert text == ("The committee summoned the Treasury over delayed county disbursements [1][2]. "
                    "Wanjiru said fourteen counties could not pay health workers [3].")
    assert [c["ref"] for c in citations] == ["aaaa1111", "bbbb2222", "cccc3333"]
    assert citations[0]["url"] == "https://n.example/1" and citations[0]["platform"] == "Daily Nation"


def _writer(monkeypatch, sentences):
    seen = []

    def answer(instructions, untrusted, expected_keys, **kw):
        seen.append(untrusted)
        return {"sentences": sentences}

    monkeypatch.setattr(llm, "call_json_untrusted", answer)
    return seen


def test_the_brief_is_written_from_the_record_and_its_inventions_removed(monkeypatch):
    seen = _writer(monkeypatch, [
        {"text": "The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
         "facts": ["F1"]},
        {"text": "Jane Wanjiru said fourteen counties could not pay health workers.", "facts": ["F2"]},
        {"text": "Opposition senators alleged that the funds were diverted to a private account.",
         "facts": ["F3"]},
        {"text": "Wanjiru is the frontrunner for the 2027 governorship.", "facts": ["F2"]},
    ])
    result = from_facts.brief_from_record(SUBJECT, {"findings": FINDINGS})
    assert result["used"] is True
    assert "frontrunner" not in result["text"]
    assert result["check"]["kept"] == 3
    assert result["check"]["removed"][0]["sentence"].startswith("Wanjiru is the frontrunner")
    assert "F3 [ALLEGED" in seen[0], "the writer was not told which findings are only alleged"


def test_a_thin_record_keeps_the_brief_it_had_and_says_why(monkeypatch):
    _writer(monkeypatch, [])
    result = from_facts.brief_from_record(SUBJECT, {"findings": FINDINGS[:1]})
    assert result["used"] is False and "finding" in result["reason"]


def test_too_little_surviving_the_check_keeps_the_brief_it_had(monkeypatch):
    _writer(monkeypatch, [{"text": "Wanjiru will win in 2027.", "facts": ["F2"]},
                          {"text": "The economy is collapsing.", "facts": ["F1"]}])
    result = from_facts.brief_from_record(SUBJECT, {"findings": FINDINGS})
    assert result["used"] is False
    assert result["check"]["kept"] == 0 and len(result["check"]["removed"]) == 2


@pytest.mark.parametrize("sentence", [
    "Separately, The Standard reported that Treasury officials were summoned by the committee.",
])
def test_an_outlet_named_in_the_cited_sources_text_is_allowed(sentence):
    assert _check(sentence, ["F1"]) is None


def test_a_sentence_with_nothing_checkable_in_it_is_removed():
    """Once the subject's name and the year are set aside, "Wanjiru will win
    in 2027" has no content to compare — and so passed every ratio."""
    assert _check("Wanjiru will win in 2027.", ["F2"]) is not None


def test_a_year_the_findings_do_not_give_is_removed():
    reason = _check("In 2019 the Senate Finance Committee summoned the Treasury over delayed county disbursements.",
                    ["F1"])
    assert reason and "2019" in reason
