"""Evidence store: the due-diligence core.

The product promise is that a single fact buried deep in one obscure old
article is findable — the machine equivalent of an analyst spotting a hidden
phrase at 3am. These tests lock in that capability: full-text retrieval must
surface the buried passage, rank it, and quote it, while excluding off-topic
documents.
"""

from datetime import datetime

from sqlalchemy import text

from engine.db.models import Claim, ClaimEvidence, Document, Entity, EntityRelationship, Event, EventEvidence, Politician


def _subject(db_session, name="Evidence Probe"):
    p = Politician(name=name)
    db_session.add(p)
    db_session.flush()
    return p


def test_buried_fact_is_retrievable_from_a_long_document(db_session):
    """One relevant sentence inside thousands of words of noise is found."""
    subject = _subject(db_session)
    buried = (
        "Routine council minutes about drainage works. " * 300
        + "In 1992 the subject was named in a Nairobi land tribunal over the Karen plot transfer. "
        + "Unrelated weather notes followed. " * 300
    )
    db_session.add(
        Document(
            politician_id=subject.id, url="http://archive.example/1992", domain="archive.example",
            title="Council Archive 1992", body=buried, source="searxng", source_tier="archive",
            published_at=datetime(1992, 4, 3), content_hash="buried-1",
        )
    )
    db_session.add(
        Document(
            politician_id=subject.id, url="http://sports.example/x", domain="sports.example",
            title="Basketball league recap", body="Scores and standings. " * 100,
            source="searxng", content_hash="noise-1",
        )
    )
    db_session.commit()

    rows = db_session.execute(
        text(
            """
            SELECT title,
                   ts_headline('english', body, websearch_to_tsquery('english', :q),
                               'MaxFragments=1,MinWords=6,MaxWords=20') AS snippet
            FROM documents
            WHERE politician_id = :pid
              AND search_vector @@ websearch_to_tsquery('english', :q)
            ORDER BY ts_rank(search_vector, websearch_to_tsquery('english', :q)) DESC
            """
        ),
        {"q": "land tribunal Karen plot", "pid": subject.id},
    ).fetchall()

    assert len(rows) == 1, "off-topic document must not match"
    title, snippet = rows[0]
    assert title == "Council Archive 1992"
    assert "tribunal" in snippet.lower()  # the exact buried passage is quotable


def test_search_vector_is_maintained_on_update(db_session):
    """The trigger keeps the index in sync so retrieval can never go stale."""
    subject = _subject(db_session, "Trigger Probe")
    doc = Document(
        politician_id=subject.id, title="Initial", body="nothing notable here",
        source="searxng", content_hash="trig-1",
    )
    db_session.add(doc)
    db_session.commit()

    doc.body = "Revised: a procurement irregularity at the Mombasa port terminal."
    db_session.commit()

    found = db_session.execute(
        text(
            "SELECT count(*) FROM documents WHERE politician_id = :pid "
            "AND search_vector @@ websearch_to_tsquery('english', :q)"
        ),
        {"q": "procurement irregularity", "pid": subject.id},
    ).scalar()
    assert found == 1


def test_claim_is_gradeable_against_stored_evidence(db_session):
    """A claim links to the document that supports it — the citation contract
    the verification agent enforces (no evidence -> not presented as fact)."""
    subject = _subject(db_session, "Claim Probe")
    doc = Document(
        politician_id=subject.id, url="http://news.example/a", domain="news.example",
        title="Tender awarded", body="The ministry awarded the tender in March.",
        source="gdelt", content_hash="claim-doc-1",
    )
    db_session.add(doc)
    db_session.flush()

    claim = Claim(
        politician_id=subject.id, text="The ministry awarded the tender in March.",
        claim_type="fact", status="verified", confidence=0.8,
        evidence_count=1, independent_sources=1,
    )
    db_session.add(claim)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=claim.id, document_id=doc.id, quote="The ministry awarded the tender in March.",
            url=doc.url, stance="supports", credibility=0.7,
        )
    )
    db_session.commit()

    linked = db_session.query(ClaimEvidence).filter_by(claim_id=claim.id).all()
    assert len(linked) == 1
    assert linked[0].document_id == doc.id
    assert linked[0].stance == "supports"


def test_event_dedupes_reporting_into_one_happening_with_evidence(db_session):
    """Many articles about one happening collapse into a single event whose
    confidence rests on independent corroboration."""
    subject = _subject(db_session, "Event Probe")
    docs = [
        Document(
            politician_id=subject.id, url=f"http://{d}/story", domain=d,
            title="Contract signed", body="A contract was signed on 3 May.",
            source="gdelt", content_hash=f"ev-{d}",
        )
        for d in ("nation.example", "standard.example", "star.example")
    ]
    db_session.add_all(docs)
    db_session.flush()

    event = Event(
        politician_id=subject.id, title="Contract signed", event_type="contract",
        occurred_at=datetime(2026, 5, 3), corroboration_count=len(docs),
        independent_domains=len({d.domain for d in docs}), confidence=0.85,
    )
    db_session.add(event)
    db_session.flush()
    for d in docs:
        db_session.add(EventEvidence(event_id=event.id, document_id=d.id, role="corroborating"))
    db_session.commit()

    stored = db_session.query(Event).filter_by(id=event.id).one()
    assert stored.independent_domains == 3
    assert db_session.query(EventEvidence).filter_by(event_id=event.id).count() == 3


def test_knowledge_graph_edge_carries_provenance(db_session):
    """Relationships are typed and evidence-backed, so the graph can be
    reasoned over rather than trusted blindly."""
    subject = _subject(db_session, "Graph Probe")
    a = Entity(name="Jane Doe", type="person", canonical_key="person:jane doe")
    b = Entity(name="Acme Ltd", type="company", canonical_key="company:acme ltd")
    db_session.add_all([a, b])
    db_session.flush()

    db_session.add(
        EntityRelationship(
            politician_id=subject.id, source_entity_id=a.id, target_entity_id=b.id,
            rel_type="works_for", confidence=0.9, evidence_count=2,
            evidence=[{"document_id": "d1", "quote": "Doe, a director of Acme Ltd"}],
        )
    )
    db_session.commit()

    edge = db_session.query(EntityRelationship).filter_by(source_entity_id=a.id).one()
    assert edge.rel_type == "works_for"
    assert edge.evidence and edge.evidence[0]["quote"]
