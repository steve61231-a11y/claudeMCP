"""The page told an operator to change a setting they had already changed.

When the grounding check could not use the entity model it reported
`method: "capitalised-names"` and nothing else, so the page had to guess why
and guessed the common case: "set LOW_MEMORY=false for the fuller entity
check". Said to somebody who had already set LOW_MEMORY=false, that is a
dead end dressed as a fix — and there was no way to confirm the setting had
taken effect short of generating a whole report and reading that same
guessed banner.

This repository has paid for the identical mistake twice already: an
operator sent to a Railway "Networking" menu that could not exist for a
crashed service, and a nightly backup diagnosed as holding the wrong
hostname when the real cause was a TLS refusal. Both cost an evening, and
both were a cause stated without being checked.

So: three separable causes, three different remedies, and nothing guesses.

    LOW_MEMORY is on            → a setting the operator controls
    the model is not loadable   → a missing build step; no setting fixes it
    loading it raised           → the exception, verbatim

And `/api/health` answers it from one URL: the setting as parsed, whether
the model actually loads, and which extractor the check will therefore use.
The setting as PARSED, deliberately — a typo in the value is exactly the
failure this is for.
"""

import pytest

from engine.config import settings
from engine.reports import grounding

PAYLOAD = {"brief": "Musalia Mudavadi and Peter Kamau disagreed about the Treasury."}
MENTIONS = [{"text": "Musalia Mudavadi spoke about the National Treasury today."}]


def test_the_setting_is_parsed_from_every_spelling_of_false(monkeypatch):
    """The first thing to rule out, and the cheapest. A value that does not
    parse would leave the setting on while the dashboard says it is off."""
    from pydantic_settings import BaseSettings

    for value in ("false", "False", "FALSE", "0", "no"):
        monkeypatch.setenv("LOW_MEMORY", value)

        class Probe(BaseSettings):
            low_memory: bool = True

        assert Probe().low_memory is False, f"LOW_MEMORY={value} did not parse"


def test_low_memory_on_is_reported_as_a_setting_problem(monkeypatch):
    monkeypatch.setattr(settings, "low_memory", True)
    import engine.processing.entities as entities
    monkeypatch.setattr(entities, "_nlp", None)

    result = grounding.check(PAYLOAD, MENTIONS)
    assert result["method"] == "capitalised-names"
    assert "LOW_MEMORY is on" in result["method_reason"]
    assert "LOW_MEMORY=false" in result["method_reason"], (
        "the remedy is not stated, so a reader has to go and find it")


def test_a_missing_model_is_not_reported_as_a_setting_problem(monkeypatch):
    """The failure that started this. LOW_MEMORY is already false and the
    model still is not there, so telling the operator to set LOW_MEMORY
    sends them round a loop."""
    import spacy

    import engine.processing.entities as entities

    monkeypatch.setattr(settings, "low_memory", False)
    monkeypatch.setattr(entities, "_nlp", None)
    monkeypatch.setattr(spacy, "load",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("[E050]")))

    result = grounding.check(PAYLOAD, MENTIONS)
    assert result["method"] == "capitalised-names"
    reason = result["method_reason"]
    assert "not a settings problem" in reason
    assert "en_core_web_sm" in reason, "the actual cause is not named"
    assert "Set LOW_MEMORY=false" not in reason, (
        "it is still advising a setting that is already correct")


def test_the_working_path_claims_no_problem_at_all(monkeypatch):
    """A reason attached to a check that worked would be noise that teaches
    a reader to ignore the field."""
    import engine.processing.entities as entities

    monkeypatch.setattr(settings, "low_memory", False)
    monkeypatch.setattr(entities, "_nlp", None)
    try:
        import spacy
        spacy.load("en_core_web_sm")
    except Exception:
        pytest.skip("en_core_web_sm is not installed here")

    result = grounding.check(PAYLOAD, MENTIONS)
    assert result["method"] == "ner"
    assert "method_reason" not in result


def test_the_entity_model_checks_more_names_than_the_pattern(monkeypatch):
    """The reason any of this matters. If the two extractors found the same
    names there would be nothing to advise anybody about."""
    import engine.processing.entities as entities

    try:
        import spacy
        spacy.load("en_core_web_sm")
    except Exception:
        pytest.skip("en_core_web_sm is not installed here")

    monkeypatch.setattr(settings, "low_memory", False)
    monkeypatch.setattr(entities, "_nlp", None)
    rich = grounding.check(PAYLOAD, MENTIONS)

    monkeypatch.setattr(settings, "low_memory", True)
    monkeypatch.setattr(entities, "_nlp", None)
    cheap = grounding.check(PAYLOAD, MENTIONS)

    assert rich["checked"] > cheap["checked"], (
        "the entity model is no more thorough than the pattern, so the "
        "advice to enable it is not worth giving")
    # Both must still catch the invented name — the advice is about depth,
    # never about whether the check works at all.
    assert "Peter Kamau" in rich["ungrounded"]
    assert "Peter Kamau" in cheap["ungrounded"]


# --- confirmable without generating a report ---------------------------------

def test_health_reports_what_the_deployment_can_actually_do(monkeypatch):
    from engine.api_server import _analysis_capability

    monkeypatch.setattr(settings, "low_memory", True)
    import engine.processing.entities as entities
    monkeypatch.setattr(entities, "_nlp", None)

    out = _analysis_capability()
    assert out["low_memory"] is True
    assert out["entity_model"] == "blank"
    assert out["grounding_method"] == "capitalised-names"
    assert "LOW_MEMORY is on" in out["hint"]


def test_health_does_not_blame_the_setting_when_the_setting_is_right(monkeypatch):
    import spacy

    import engine.processing.entities as entities
    from engine.api_server import _analysis_capability

    monkeypatch.setattr(settings, "low_memory", False)
    monkeypatch.setattr(entities, "_nlp", None)
    monkeypatch.setattr(spacy, "load",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("[E050]")))

    out = _analysis_capability()
    assert out["low_memory"] is False
    assert out["entity_model"] == "blank"
    assert "already off" in out["hint"]
    assert "spacy download" in out["hint"], (
        "the remedy is not named, so the operator is told what is wrong and "
        "not what to do")


def test_the_page_prints_the_engine_s_reason_rather_than_its_own(monkeypatch):
    """The banner's old text was hard-coded advice. The engine knows the
    cause; the page's job is to show it."""
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    banner = page[page.index("function groundingBanner("):]
    banner = banner[:banner.index("\n  }\n")]
    assert "gr.method_reason" in banner, (
        "the page is not showing the engine's reason")
    assert "set LOW_MEMORY=false for the fuller entity check" not in page, (
        "the hard-coded guess is still on the page")


# --- did the deploy's migration actually run? --------------------------------

def test_health_reports_whether_the_schema_is_at_head(db_session):
    """`start.sh` starts the server even when `alembic upgrade head` fails,
    deliberately — a container that vanishes tells an operator nothing.

    That has a consequence worth being able to see. The app then runs
    against an older schema than its models declare, and the first symptom
    is every mention INSERT failing on a column that does not exist, which
    surfaces to the reader as "collection returned nothing" — a sentence
    about the subject rather than about the deploy. The exact confusion this
    codebase exists to prevent, one layer below where it usually happens.
    """
    from engine.api_server import _schema_state

    state = _schema_state(db_session)
    assert state.get("code_head"), "the code's head revision is not reported"
    # The test fixture migrates to head, so this deployment is current.
    assert state["migrated"] is True, state
    assert "hint" not in state, "a migrated database is being given a remedy"


def test_a_database_behind_the_code_is_reported_with_its_remedy(monkeypatch):
    """The case that matters: a push that included a migration, and a deploy
    whose alembic step failed."""
    import engine.api_server as api

    class _Behind:
        def execute(self, *a, **k):
            class R:
                @staticmethod
                def fetchall():
                    return [("an-older-revision",)]
            return R()

    state = api._schema_state(_Behind())
    assert state["migrated"] is False
    assert state["database_at"] == "an-older-revision"
    assert "older schema" in state["hint"]
    assert "alembic" in state["hint"], (
        "the remedy does not name the step to look at, so the operator is "
        "told what is wrong and not what to do")
