from datetime import datetime

from engine import llm
from engine.config import settings
from engine.db.models import AuthorProfile, Entity, MentionEntity, Politician, RawMention
from engine.pipeline import _classify_influencers, _upsert_entity, run_analysis
from engine.processing import entities as entities_module
from engine.tests.test_pipeline import patch_pipeline_dependencies

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59))


def make_politician(db):
    politician = Politician(name="John Mbadi", aliases=["Mbadi"], keywords=["Treasury"])
    db.add(politician)
    db.commit()
    return politician


def add_mention(db, politician, text, author="user1", platform="tiktok", followers=None):
    mention = RawMention(
        politician_id=politician.id,
        platform=platform,
        source_type="post",
        author_handle=author,
        text=text,
        posted_at=datetime(2026, 6, 15),
        engagement_json={"likes": 3},
        content_hash=f"hash-{author}-{hash(text)}",
        is_spam=0,
        link_checked=0,
    )
    db.add(mention)
    if followers is not None:
        db.add(AuthorProfile(platform=platform, handle=author, follower_count=followers))
    db.commit()
    return mention


def test_extract_people_skips_llm_without_ner_candidates(monkeypatch):
    monkeypatch.setattr(entities_module, "extract_standard_entities", lambda text: [])

    def boom(*a, **k):
        raise AssertionError("LLM must not be called without NER candidates")

    monkeypatch.setattr(llm, "call_json_untrusted", boom)
    assert entities_module.extract_people("no names here", "John Mbadi") == []


def test_extract_people_filters_the_tracked_politician(monkeypatch):
    monkeypatch.setattr(
        entities_module,
        "extract_standard_entities",
        lambda text: [{"name": "Linus Kaikai", "type": "person"}],
    )
    monkeypatch.setattr(
        llm,
        "call_json_untrusted",
        lambda *a, **k: {
            "people": [
                {"name": "Linus Kaikai", "role": "journalist", "affiliation": "Citizen TV"},
                {"name": "John Mbadi", "role": "politician", "affiliation": "ODM"},
            ]
        },
    )
    people = entities_module.extract_people("text", "John Mbadi")
    assert people == [{"name": "Linus Kaikai", "role": "journalist", "affiliation": "Citizen TV"}]


def test_run_analysis_maps_people_and_writes_graph_edges(db_session, monkeypatch):
    fake_driver = patch_pipeline_dependencies(monkeypatch)
    monkeypatch.setattr(
        entities_module,
        "extract_people",
        lambda text, name: [{"name": "Linus Kaikai", "role": "journalist", "affiliation": "Citizen TV"}]
        if "Kaikai" in text
        else [],
    )
    politician = make_politician(db_session)
    add_mention(db_session, politician, "Mbadi interviewed by Linus Kaikai on Citizen TV")

    run_analysis(db_session, politician, "weekly", *WINDOW)

    person = db_session.query(Entity).filter_by(type="person").one()
    assert person.name == "Linus Kaikai"
    assert person.role == "journalist"
    assert person.affiliation == "Citizen TV"
    assert person.canonical_key == "person:linus kaikai"
    assert db_session.query(MentionEntity).filter_by(entity_id=person.id).count() == 1
    graph_queries = " ".join(q for q, _ in fake_driver.recorded_calls)
    assert "MERGE (person:Person {canonical_key: $canonical_key})" in graph_queries


def test_influencer_threshold_boundary(db_session, monkeypatch):
    monkeypatch.setattr(settings, "influencer_follower_threshold", 1000)
    politician = make_politician(db_session)
    below = add_mention(db_session, politician, "Mbadi clip one", author="smallfry", followers=999)
    above = add_mention(db_session, politician, "Mbadi clip two", author="bigshot", followers=1001)

    influencers = _classify_influencers(db_session, [below, above])

    assert [i["handle"] for i in influencers] == ["bigshot"]
    assert influencers[0]["followers"] == 1001
    assert influencers[0]["posts"] == 1


def test_upsert_entity_dedupes_by_canonical_key_and_fills_gaps(db_session):
    first = _upsert_entity(db_session, "person", "Babu Owino")
    second = _upsert_entity(db_session, "person", "babu owino", role="politician", affiliation="ODM")
    assert first.id == second.id
    assert second.role == "politician"
    assert db_session.query(Entity).filter_by(type="person").count() == 1
