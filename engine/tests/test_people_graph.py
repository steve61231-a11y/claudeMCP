from datetime import datetime, timedelta

from engine.db.models import Entity, MentionEntity, MentionSentiment, Politician, RawMention
from engine.intelligence import people_graph


def _setup(db, monkeypatch):
    # No LLM in unit tests: classification/profiles return empty.
    monkeypatch.setattr(people_graph, "_classify_pairs", lambda *a, **k: [])
    monkeypatch.setattr(people_graph, "_profile_people", lambda *a, **k: {})
    p = Politician(name="Subject Pol", aliases=[], keywords=[])
    db.add(p)
    db.flush()
    subject_entity = Entity(name="Subject Pol", type="politician", canonical_key="politician:subject pol")
    db.add(subject_entity)
    db.flush()
    return p, subject_entity


def _mention(db, p, text, when, author="a1", engagement=None):
    m = RawMention(
        politician_id=p.id, platform="tiktok", source_type="post", author_handle=author,
        text=text, posted_at=when, engagement_json=engagement or {}, raw_payload={},
        content_hash=f"h{author}{when.isoformat()}{text[:8]}", is_spam=0,
    )
    db.add(m)
    db.flush()
    return m


def _link(db, m, entity):
    db.add(MentionEntity(mention_id=m.id, entity_id=entity.id, confidence=0.9, match_type="direct"))


def test_influence_engagement_weighting_and_pair_edges(db_session, monkeypatch):
    db = db_session
    p, subject = _setup(db, monkeypatch)
    now = datetime(2026, 7, 1)
    big = Entity(name="Big Name", type="person", canonical_key="person:big name")
    small = Entity(name="Small Name", type="person", canonical_key="person:small name")
    db.add_all([big, small])
    db.flush()

    m1 = _mention(db, p, "Big Name and Small Name with Subject Pol", now - timedelta(days=1), engagement={"views": 100000})
    m2 = _mention(db, p, "Big Name again", now - timedelta(days=2), engagement={"views": 50000})
    m3 = _mention(db, p, "Small Name quietly", now - timedelta(days=20), engagement={"likes": 2})
    for m in (m1, m2, m3):
        _link(db, m, subject)
    _link(db, m1, big); _link(db, m2, big)
    _link(db, m1, small); _link(db, m3, small)
    db.add(MentionSentiment(mention_id=m1.id, sentiment="negative", intensity=0.5, confidence=0.9, source="t"))
    db.commit()

    g = people_graph.build_people_graph(db, p, now=now)
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["Big Name"]["influence"] > nodes["Small Name"]["influence"]
    assert nodes["Big Name"]["recent_activity"] is True
    # Small Name appears in m1 (recent) so it is also active.
    assert nodes["Small Name"]["recent_activity"] is True
    # Both share exactly one mention: below the >=2 pair threshold, no pair edge.
    pair = [e for e in g["edges"] if {e["source"], e["target"]} == {"Big Name", "Small Name"}]
    assert pair == []
    # Subject spokes exist for both people.
    assert any(e["source"] == "subject" and e["target"] == "Big Name" for e in g["edges"])


def test_stance_classification(db_session, monkeypatch):
    db = db_session
    p, subject = _setup(db, monkeypatch)
    now = datetime(2026, 7, 1)
    critic = Entity(name="Critic Person", type="person", canonical_key="person:critic person")
    db.add(critic)
    db.flush()
    for i in range(4):
        m = _mention(db, p, f"clash {i}", now - timedelta(days=i + 1), author=f"x{i}")
        _link(db, m, subject); _link(db, m, critic)
        db.add(MentionSentiment(mention_id=m.id, sentiment="negative", intensity=0.6, confidence=0.9, source="t"))
    db.commit()

    g = people_graph.build_people_graph(db, p, now=now)
    node = next(n for n in g["nodes"] if n["id"] == "Critic Person")
    assert node["stance"] == "critic-context"
