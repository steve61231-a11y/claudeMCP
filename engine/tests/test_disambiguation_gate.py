"""Relevance & disambiguation gate.

Discovery is deliberately broad, so the gate is what keeps breadth from becoming
noise. The rules these tests pin down:
  - a page that never names the subject is rejected without spending a token,
  - a full-name match with corroborating context is accepted the same way,
  - only the genuinely ambiguous middle reaches the model,
  - a model failure never deletes evidence,
  - and off-topic documents are excluded from the analysis corpus.
"""

from datetime import datetime

from engine.agents import disambiguate
from engine.agents.disambiguate import AMBIGUOUS, OFF_TOPIC, ON_TOPIC, build_profile, score_document
from engine.db.models import Document, Politician


def _subject(db_session, **kwargs):
    defaults = dict(
        name="John Mbadi",
        aliases=["Mbadi"],
        titles=["Treasury CS"],
        keywords=["Kenya", "Treasury", "ODM"],
        swahili_terms=[],
    )
    defaults.update(kwargs)
    p = Politician(**defaults)
    db_session.add(p)
    db_session.flush()
    return p


def _doc(db_session, subject, title, body, h):
    d = Document(
        politician_id=subject.id, title=title, body=body, content_hash=h,
        source="searxng", domain="example.com", fetched_at=datetime.utcnow(),
    )
    db_session.add(d)
    return d


# --- deterministic scoring ------------------------------------------------

def test_document_that_never_names_the_subject_is_rejected():
    profile = build_profile(Politician(name="John Mbadi", aliases=[], titles=[], keywords=[], swahili_terms=[]))
    score, reason = score_document("A recipe for sourdough bread.", "Baking", profile)
    assert score == 0.0
    assert "never named" in reason


def test_full_name_with_context_scores_high():
    profile = build_profile(
        Politician(name="John Mbadi", aliases=[], titles=["Treasury CS"], keywords=["Kenya"], swahili_terms=[])
    )
    score, _ = score_document("Treasury CS John Mbadi addressed Kenya's budget.", "Budget", profile)
    assert score >= 0.9


def test_bare_surname_alone_is_not_enough():
    """The homonym trap: a surname with no corroboration must not be trusted."""
    profile = build_profile(
        Politician(name="John Mbadi", aliases=[], titles=["Treasury CS"], keywords=["Kenya"], swahili_terms=[])
    )
    score, reason = score_document("Mbadi scored twice in the second half.", "Match report", profile)
    assert score <= 0.5
    assert "surname" in reason


def test_acronym_subject_needs_context_to_qualify():
    """'SHA' means Kenyan health policy here — and something else elsewhere."""
    profile = build_profile(
        Politician(name="SHA", aliases=["Social Health Authority"], titles=[],
                   keywords=["Kenya", "health", "insurance"], swahili_terms=[])
    )
    on_topic, _ = score_document(
        "The Social Health Authority rolled out cover across Kenya.", "Health", profile
    )
    assert on_topic >= 0.8

    # A different domain that merely uses the token: not clearly ours.
    other, _ = score_document("SHA-256 is a cryptographic hash function.", "Crypto", profile)
    assert other <= 0.5


# --- gate behaviour --------------------------------------------------------

def test_gate_resolves_clear_cases_without_calling_the_model(db_session, monkeypatch):
    subject = _subject(db_session)
    _doc(db_session, subject, "Budget", "Treasury CS John Mbadi presented Kenya's budget.", "g1")
    _doc(db_session, subject, "Baking", "How to bake sourdough bread at home.", "g2")
    db_session.commit()

    def fail(*a, **k):
        raise AssertionError("clear-cut documents must not reach the model")

    monkeypatch.setattr(disambiguate, "_adjudicate_batch", fail)
    stats = disambiguate.gate_documents(db_session, subject)

    assert stats["on_topic"] == 1
    assert stats["off_topic"] == 1
    assert stats["adjudicated"] == 0


def test_ambiguous_documents_are_adjudicated_by_the_model(db_session, monkeypatch):
    subject = _subject(db_session)
    doc = _doc(db_session, subject, "Sports", "Mbadi scored twice on Saturday.", "g3")
    db_session.commit()

    monkeypatch.setattr(
        disambiguate,
        "_adjudicate_batch",
        lambda profile, items: {
            items[0][0]: {"verdict": OFF_TOPIC, "confidence": 0.9, "reason": "a footballer, not the CS"}
        },
    )
    stats = disambiguate.gate_documents(db_session, subject)

    db_session.refresh(doc)
    assert stats["adjudicated"] == 1
    assert doc.relevance_verdict == OFF_TOPIC
    assert "footballer" in doc.relevance_reason


def test_model_failure_keeps_evidence_rather_than_dropping_it(db_session, monkeypatch):
    """Losing evidence is worse than keeping a doubtful item flagged."""
    subject = _subject(db_session)
    doc = _doc(db_session, subject, "Sports", "Mbadi scored twice on Saturday.", "g4")
    db_session.commit()

    monkeypatch.setattr(disambiguate, "_adjudicate_batch", lambda profile, items: {})
    disambiguate.gate_documents(db_session, subject)

    db_session.refresh(doc)
    assert doc.relevance_verdict == AMBIGUOUS
    assert doc.relevance_verdict != OFF_TOPIC


def test_gate_is_idempotent(db_session, monkeypatch):
    subject = _subject(db_session)
    _doc(db_session, subject, "Budget", "Treasury CS John Mbadi presented Kenya's budget.", "g5")
    db_session.commit()
    monkeypatch.setattr(disambiguate, "_adjudicate_batch", lambda profile, items: {})

    first = disambiguate.gate_documents(db_session, subject)
    second = disambiguate.gate_documents(db_session, subject)

    assert first["examined"] == 1
    assert second["examined"] == 0, "already-gated documents must not be re-examined"


def test_off_topic_documents_are_excluded_from_the_analysis_corpus(db_session, monkeypatch):
    """The gate only matters if its verdicts actually change what gets analysed."""
    from engine.pipeline import _document_corpus

    subject = _subject(db_session)
    _doc(db_session, subject, "Budget", "Treasury CS John Mbadi presented Kenya's budget.", "g6")
    _doc(db_session, subject, "Baking", "How to bake sourdough bread at home.", "g7")
    db_session.commit()
    monkeypatch.setattr(disambiguate, "_adjudicate_batch", lambda profile, items: {})
    disambiguate.gate_documents(db_session, subject)

    corpus = _document_corpus(db_session, subject, datetime(2020, 1, 1), datetime(2030, 1, 1))

    assert len(corpus) == 1
    assert "budget" in corpus[0]["text"].lower()
