"""1 name in this report does not appear in any source: sio utumwa'

A live report on Rigathi Gachagua, build 06c1a53, opened with a red banner
telling a CEO the report had invented a name and must not be sent to a
client. The "name" was `sio utumwa'` — a Swahili phrase from a quoted
slogan, "not slavery", with the closing quote mark caught in the token.

Two faults, and both are this file's job:

  * the token pattern allows an apostrophe INSIDE a word, for O'Neill and
    Ng'ang'a, so a trailing quote rode along — and `utumwa'` with its quote
    matched nothing in a corpus that says `utumwa` all over the slogan the
    report was quoting;
  * the English entity model, handed Swahili, labels what it cannot parse a
    PERSON. A phrase with no capital letter in it is not a name in English
    prose, whatever a model labels it.

And a third, which is the product's: the banner reported and then stopped.
It told an operator "do not send this until it is clean" and left the
cleaning to them, in a product whose reader is a CEO who never opens a
diagnostics panel. A sentence naming someone in no source is now removed,
and the removal is declared, with the sentence, in the operator view.
"""

from engine.reports import grounding

CORPUS = [{"text": "Wafuasi waliimba 'sio utumwa' nje ya mahakama. Rigathi Gachagua "
                   "addressed supporters in Nyeri on Sunday."}]


def test_a_trailing_quote_is_not_part_of_the_name():
    assert grounding._tokens("sio utumwa'") == ["sio", "utumwa"]
    assert grounding._tokens("“Rigathi Gachagua”") == ["rigathi", "gachagua"]


def test_an_apostrophe_inside_a_name_is_kept():
    """The reason the pattern allows apostrophes at all."""
    assert grounding._tokens("Ng'ang'a O'Neill") == ["ng'ang'a", "o'neill"]


def test_a_phrase_with_no_capital_is_not_a_name():
    ok, why = grounding.is_checkable("sio utumwa'")
    assert not ok and why == "not a proper name"
    # But a particle before a capitalised surname is a name.
    assert grounding.is_checkable("de Gaulle")[0]


def test_the_live_false_positive_is_not_reported(monkeypatch):
    monkeypatch.setattr(grounding, "_names_in",
                        lambda strings: ({"sio utumwa'": "sio utumwa'"}, "ner", ""))
    result = grounding.check({"brief": "Supporters chanted 'sio utumwa' outside the court."},
                             CORPUS)
    assert result["ungrounded"] == []
    assert "not a proper name" in result["not_checkable_reasons"]


def test_a_reported_name_carries_no_stray_punctuation(monkeypatch):
    monkeypatch.setattr(grounding, "_names_in",
                        lambda strings: ({"peter kamau'": "Peter Kamau'"}, "ner", ""))
    result = grounding.check({"brief": "x" * 60}, CORPUS)
    assert result["ungrounded"] == ["Peter Kamau"]


# --- acted on, not only reported --------------------------------------------

def _invented(monkeypatch):
    monkeypatch.setattr(grounding, "_names_in",
                        lambda strings: ({"peter kamau": "Peter Kamau"}, "ner", ""))


def test_a_sentence_naming_someone_in_no_source_is_removed(monkeypatch):
    _invented(monkeypatch)
    payload = {"executive_brief": (
        "Gachagua's support held in Nyeri through the period. Peter Kamau, a "
        "close ally, is said to be organising the counties. The rally drew a "
        "large crowd on Sunday.")}
    verdict = grounding.redact(payload, grounding.check(payload, CORPUS))

    assert "Peter Kamau" not in payload["executive_brief"]
    assert "support held in Nyeri" in payload["executive_brief"], "removed too much"
    assert "large crowd on Sunday" in payload["executive_brief"]
    assert verdict["removed"] == 1


def test_every_removal_is_declared_with_its_sentence(monkeypatch):
    """The founding rule: what was taken out, and why, is on the record."""
    _invented(monkeypatch)
    payload = {"narrative_deep_dives": [{"how_it_unfolded":
        "It began in Nyeri. Peter Kamau then carried it to Murang'a and beyond, "
        "according to the analyst."}]}
    verdict = grounding.redact(payload, grounding.check(payload, CORPUS))

    (removal,) = verdict["removed_sentences"]
    assert removal["name"] == "Peter Kamau"
    assert "Peter Kamau then carried it" in removal["sentence"]
    assert removal["field"].startswith("narrative_deep_dives[0]")
    assert verdict["touched_fields"] == ["narrative_deep_dives"]


def test_a_single_unverified_word_is_never_cut(monkeypatch):
    """`is_accusable`'s asymmetry, kept: "Videos" at the start of a sentence
    looks exactly like an unknown surname, and cutting it would remove a
    good sentence to fix a problem that was never there."""
    monkeypatch.setattr(grounding, "_names_in",
                        lambda strings: ({"videos": "Videos"}, "ner", ""))
    payload = {"brief": "Videos from the rally circulated widely on Sunday evening."}
    verdict = grounding.redact(payload, grounding.check(payload, CORPUS))
    assert payload["brief"].startswith("Videos from the rally")
    assert not verdict.get("removed")


def test_a_clean_report_is_left_alone():
    payload = {"brief": "Rigathi Gachagua addressed supporters in Nyeri on Sunday."}
    verdict = {"checked": 1, "ungrounded": []}
    assert grounding.redact(payload, verdict) is verdict
    assert payload["brief"].startswith("Rigathi Gachagua")


def test_both_products_act_on_the_check():
    import inspect

    from engine.reports import issue_map, sections

    for module in (sections, issue_map):
        source = inspect.getsource(module)
        assert "grounding.redact(" in source, f"{module.__name__} only reports"
        assert "grounding.check(" in source


def test_the_operator_view_lists_what_was_removed():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    banner = page[page.index("function groundingBanner("):]
    banner = banner[:banner.index("function renderIssue(")]
    assert "gr.removed" in banner and "removed_sentences" in banner
    assert banner.count("op-only") >= 3, "a grounding card reaches the client view"
