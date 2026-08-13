"""Investigator agent and the discovery feedback loop.

The point of this stage is that a fixed probe list can only ever find what
someone anticipated. Real investigations deepen by following leads found IN the
evidence. So: gaps must be named, follow-up queries must be derived from what
was actually found, and the next run's discovery must actually chase them.
"""

from datetime import datetime

from engine.agents import investigator
from engine.db.models import Claim, Entity, EntityRelationship, Event, Politician
from engine.ingestion.queries import discovery_variants


def _subject(db_session, name="Investigator Probe"):
    p = Politician(name=name, aliases=[], titles=[], keywords=[], swahili_terms=[],
                   investigation_leads=[])
    db_session.add(p)
    db_session.flush()
    return p


def test_gaps_are_identified_without_a_model(db_session, monkeypatch):
    """Gaps are structural facts about the file, so a model outage should
    degrade the wording — not lose the finding."""
    subject = _subject(db_session)
    db_session.add(Claim(politician_id=subject.id, text="Subject owns a Swiss account",
                         status="unverified", section="risks"))
    db_session.add(Event(politician_id=subject.id, title="Alleged secret meeting",
                         dedupe_key="d1", independent_domains=1, first_seen=datetime.utcnow()))
    db_session.commit()

    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(investigator.llm, "call_json_untrusted", boom)
    result = investigator.build_agenda(db_session, subject)

    assert result["agenda"], "an outage must not empty the agenda"
    questions = " ".join(a["question"] for a in result["agenda"]).lower()
    assert "swiss account" in questions or "evidence" in questions
    assert any("corroborate" in a["question"].lower() for a in result["agenda"])


def test_agenda_is_ordered_by_what_would_change_the_picture(db_session, monkeypatch):
    subject = _subject(db_session)
    db_session.add(Event(politician_id=subject.id, title="Something", dedupe_key="d2",
                         independent_domains=1, first_seen=datetime.utcnow()))
    db_session.commit()

    monkeypatch.setattr(
        investigator.llm, "call_json_untrusted",
        lambda *a, **k: {"agenda": [
            {"question": "Low priority thing", "why": "w", "priority": "low", "suggested_query": "q1"},
            {"question": "High priority thing", "why": "w", "priority": "high", "suggested_query": "q2"},
            {"question": "Medium thing", "why": "w", "priority": "medium", "suggested_query": "q3"},
        ]},
    )
    result = investigator.build_agenda(db_session, subject)

    assert [a["priority"] for a in result["agenda"]] == ["high", "medium", "low"]


def test_unexplained_entities_become_follow_up_queries(db_session, monkeypatch):
    """A name that keeps appearing beside the subject with no explanation is
    exactly the lead a fixed probe list would never chase."""
    subject = _subject(db_session)
    a = Entity(name="Investigator Probe", type="person",
               canonical_key="person:investigator probe", mention_count=10,
               first_seen=datetime.utcnow())
    b = Entity(name="Opaque Holdings", type="company",
               canonical_key="company:opaque holdings", mention_count=4,
               first_seen=datetime.utcnow())
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(EntityRelationship(
        politician_id=subject.id, source_entity_id=a.id, target_entity_id=b.id,
        rel_type="mentioned_with", evidence_count=4,
        first_seen=datetime.utcnow(), last_seen=datetime.utcnow(),
    ))
    db_session.commit()

    monkeypatch.setattr(investigator.llm, "call_json_untrusted",
                        lambda *a, **k: {"agenda": []})
    result = investigator.build_agenda(db_session, subject)

    joined = " ".join(result["follow_up_queries"])
    assert "Opaque Holdings" in joined, "an unexplained association must become a lead"


def test_leads_are_persisted_separately_from_matching_keywords(db_session):
    """Keywords are matching terms; a lead is a question. Mixing them would
    corrupt entity matching."""
    subject = _subject(db_session)
    subject.keywords = ["Kenya", "Treasury"]
    db_session.commit()

    stored = investigator.store_follow_up_queries(
        db_session, subject, ['"Probe" "Opaque Holdings"', '"Probe" court case'])

    assert stored == 2
    assert subject.keywords == ["Kenya", "Treasury"], "matching terms must be untouched"
    assert investigator.pending_leads(subject) == [
        '"Probe" "Opaque Holdings"', '"Probe" court case']


def test_next_run_actually_chases_the_leads(db_session):
    """The loop only closes if discovery uses them."""
    subject = _subject(db_session)
    investigator.store_follow_up_queries(
        db_session, subject, ['"Probe" "Opaque Holdings" director'])

    variants = discovery_variants(subject)

    assert '"Probe" "Opaque Holdings" director' in variants
    # Leads are the questions THIS file needs, so they must not be pushed off
    # the end by the generic probe list.
    assert variants.index('"Probe" "Opaque Holdings" director') < 10


def test_leads_never_leak_into_keyword_matching(db_session):
    """A lead used as a match term would link unrelated documents."""
    from engine.agents.disambiguate import build_profile

    subject = _subject(db_session)
    subject.keywords = ["Treasury"]
    investigator.store_follow_up_queries(db_session, subject, ['"Probe" fraud allegations'])

    profile = build_profile(subject)

    assert "treasury" in profile["context_terms"]
    assert not any("fraud" in term for term in profile["context_terms"]), \
        "a follow-up question must not become a matching term"


def test_empty_file_has_nothing_to_investigate(db_session, monkeypatch):
    subject = _subject(db_session, "Empty Investigator Probe")

    def fail(*a, **k):
        raise AssertionError("must not call the model with an empty file")

    monkeypatch.setattr(investigator.llm, "call_json_untrusted", fail)
    result = investigator.build_agenda(db_session, subject)

    assert result["agenda"] == []
    assert "nothing" in result["note"]


def test_invalid_priority_is_normalised(db_session, monkeypatch):
    subject = _subject(db_session)
    db_session.add(Event(politician_id=subject.id, title="X", dedupe_key="d3",
                         independent_domains=1, first_seen=datetime.utcnow()))
    db_session.commit()
    monkeypatch.setattr(
        investigator.llm, "call_json_untrusted",
        lambda *a, **k: {"agenda": [
            {"question": "Q", "why": "w", "priority": "URGENT!!", "suggested_query": "q"}]},
    )
    result = investigator.build_agenda(db_session, subject)
    assert result["agenda"][0]["priority"] == "medium"
