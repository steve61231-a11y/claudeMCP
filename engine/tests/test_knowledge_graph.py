"""Knowledge graph — long-lived memory of how things connect.

The properties worth defending: the graph compounds across runs rather than
being rebuilt, edges carry the evidence that produced them, the model can never
invent a relationship vocabulary, weak pairs stay honestly labelled, and
indirect connections are traversable — which is the question a graph answers
that a document search cannot.
"""

from datetime import datetime

from engine.agents import knowledge_graph as kg
from engine.db.models import Entity, EntityRelationship, MentionEntity, Politician, RawMention


def _subject(db_session, name="Graph Probe"):
    p = Politician(name=name, aliases=[], titles=[], keywords=[], swahili_terms=[])
    db_session.add(p)
    db_session.flush()
    return p


def _entity(db_session, name, etype="person"):
    e = Entity(name=name, type=etype, canonical_key=f"{etype}:{name.lower()}",
               first_seen=datetime.utcnow(), last_seen=datetime.utcnow())
    db_session.add(e)
    db_session.flush()
    return e


def _co_mention(db_session, subject, entities, h):
    """One source naming several entities together."""
    m = RawMention(
        politician_id=subject.id, platform="news", source_type="article",
        author_handle="a", text="text", posted_at=datetime.utcnow(),
        content_hash=h, engagement_json={}, raw_payload={},
    )
    db_session.add(m)
    db_session.flush()
    for e in entities:
        db_session.add(MentionEntity(mention_id=m.id, entity_id=e.id, confidence=1.0))
    db_session.flush()
    return m


def test_entities_named_together_become_connected(db_session, monkeypatch):
    subject = _subject(db_session)
    a, b = _entity(db_session, "Jane Doe"), _entity(db_session, "Acme Ltd", "company")
    _co_mention(db_session, subject, [a, b], "g1")
    db_session.commit()
    monkeypatch.setattr(kg, "_type_pairs", lambda subj, pairs: {})

    stats = kg.build_graph(db_session, subject, [])

    assert stats["edges"] == 1
    edge = db_session.query(EntityRelationship).one()
    assert {edge.source_entity_id, edge.target_entity_id} == {a.id, b.id}
    assert edge.evidence, "an edge must carry the evidence that produced it"


def test_weak_pairs_are_not_given_an_invented_relationship(db_session, monkeypatch):
    """Appearing together once shows association, not a specific relationship."""
    subject = _subject(db_session)
    a, b = _entity(db_session, "Jane Doe"), _entity(db_session, "Bob Roe")
    _co_mention(db_session, subject, [a, b], "g2")
    db_session.commit()

    def fail(*a, **k):
        raise AssertionError("single co-occurrence must not trigger a typing call")

    monkeypatch.setattr(kg, "_type_pairs", fail)
    kg.build_graph(db_session, subject, [])

    assert db_session.query(EntityRelationship).one().rel_type == "mentioned_with"


def test_model_cannot_invent_relationship_types(db_session, monkeypatch):
    """An open vocabulary would make the graph unqueryable."""
    subject = _subject(db_session)
    a, b = _entity(db_session, "Jane Doe"), _entity(db_session, "Acme Ltd", "company")
    for i in range(3):  # enough weight to be typed
        _co_mention(db_session, subject, [a, b], f"g3-{i}")
    db_session.commit()

    monkeypatch.setattr(
        kg, "_type_pairs",
        lambda subj, pairs: {0: {"type": "secretly_controls", "confidence": 0.9, "reason": "made up"}},
    )
    kg.build_graph(db_session, subject, [])

    assert db_session.query(EntityRelationship).one().rel_type == "mentioned_with"


def test_recognised_type_is_applied(db_session, monkeypatch):
    subject = _subject(db_session)
    a, b = _entity(db_session, "Jane Doe"), _entity(db_session, "Acme Ltd", "company")
    for i in range(3):
        _co_mention(db_session, subject, [a, b], f"g4-{i}")
    db_session.commit()

    monkeypatch.setattr(
        kg, "_type_pairs",
        lambda subj, pairs: {0: {"type": "works_for", "confidence": 0.85, "reason": "stated"}},
    )
    stats = kg.build_graph(db_session, subject, [])

    edge = db_session.query(EntityRelationship).one()
    assert edge.rel_type == "works_for"
    assert edge.confidence == 0.85
    assert stats["typed"] == 1


def test_graph_compounds_across_runs_rather_than_duplicating(db_session, monkeypatch):
    """Memory that rebuilds itself each run isn't memory."""
    subject = _subject(db_session)
    a, b = _entity(db_session, "Jane Doe"), _entity(db_session, "Acme Ltd", "company")
    _co_mention(db_session, subject, [a, b], "g5")
    db_session.commit()
    monkeypatch.setattr(kg, "_type_pairs", lambda subj, pairs: {})

    first = kg.build_graph(db_session, subject, [])
    first_seen = db_session.query(EntityRelationship).one().first_seen

    _co_mention(db_session, subject, [a, b], "g6")  # seen again in a new source
    db_session.commit()
    second = kg.build_graph(db_session, subject, [])

    edge = db_session.query(EntityRelationship).one()
    assert first["new_edges"] == 1 and second["new_edges"] == 0
    assert edge.weight == 2, "re-observing an edge strengthens it"
    assert edge.first_seen == first_seen, "first appearance must not be overwritten"


def test_indirect_connections_are_traversable(db_session, monkeypatch):
    """The question a graph answers that document search cannot: how is A
    connected to C, when nothing mentions them together?"""
    subject = _subject(db_session)
    a = _entity(db_session, "Jane Doe")
    b = _entity(db_session, "Bridge Person")
    c = _entity(db_session, "Opaque Holdings", "company")
    _co_mention(db_session, subject, [a, b], "g7")
    _co_mention(db_session, subject, [b, c], "g8")
    db_session.commit()
    monkeypatch.setattr(kg, "_type_pairs", lambda subj, pairs: {})
    kg.build_graph(db_session, subject, [])

    paths = kg.find_paths(db_session, a.id, c.id)

    assert paths, "an indirect route must be discoverable"
    assert paths[0][-1]["name"] == "Opaque Holdings"
    assert any(step["name"] == "Bridge Person" for step in paths[0]), "the intermediary is the finding"


def test_neighbours_are_ranked_by_strength(db_session, monkeypatch):
    subject = _subject(db_session)
    a = _entity(db_session, "Jane Doe")
    strong = _entity(db_session, "Close Associate")
    weak = _entity(db_session, "Passing Mention")
    for i in range(3):
        _co_mention(db_session, subject, [a, strong], f"g9-{i}")
    _co_mention(db_session, subject, [a, weak], "g10")
    db_session.commit()
    monkeypatch.setattr(kg, "_type_pairs", lambda subj, pairs: {})
    kg.build_graph(db_session, subject, [])

    found = kg.neighbours(db_session, a.id)
    assert found[0]["name"] == "Close Associate"


def test_empty_corpus_is_a_no_op(db_session):
    subject = _subject(db_session, "Empty Graph Probe")
    assert kg.build_graph(db_session, subject, [])["edges"] == 0
