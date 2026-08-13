"""Temporal intelligence and anomaly detection.

Two properties under test. First: movement is reported, not just state — and a
first run says "baseline" rather than looking like a surge. Second: the
detectors surface things volume-ranking never would — a brand-new name, a story
only one outlet carries, copy-paste posting across accounts, and unusual
silence.
"""

from datetime import datetime, timedelta

from engine.agents import anomaly, temporal
from engine.db.models import (
    Entity,
    EntityRelationship,
    Event,
    Politician,
    RawMention,
    Snapshot,
)


def _subject(db_session, name="Temporal Probe"):
    p = Politician(name=name, aliases=[], titles=[], keywords=[], swahili_terms=[])
    db_session.add(p)
    db_session.flush()
    return p


def _mention(db_session, subject, text, handle, h, hours_ago=1):
    m = RawMention(
        politician_id=subject.id, platform="twitter", source_type="post",
        author_handle=handle, text=text,
        posted_at=datetime.utcnow() - timedelta(hours=hours_ago),
        content_hash=h, engagement_json={}, raw_payload={},
    )
    db_session.add(m)
    return m


# --- temporal --------------------------------------------------------------

def test_first_snapshot_is_a_baseline_not_a_surge(db_session):
    """Nothing to compare against must not read as explosive growth."""
    subject = _subject(db_session)
    snapshot = temporal.take_snapshot(db_session, subject)

    assert snapshot.delta["baseline"] is True
    assert "nothing to compare" in snapshot.delta["note"]


def test_second_snapshot_reports_what_changed(db_session):
    subject = _subject(db_session)
    _mention(db_session, subject, "first post", "a", "t1")
    db_session.commit()
    temporal.take_snapshot(db_session, subject)

    for i in range(3):
        _mention(db_session, subject, f"later post {i}", "b", f"t2-{i}")
    db_session.commit()
    second = temporal.take_snapshot(db_session, subject)

    assert second.delta["baseline"] is False
    assert second.delta["mentions"]["change"] == 3
    assert second.delta["mentions"]["before"] == 1


def test_new_entities_are_reported_as_new(db_session):
    subject = _subject(db_session)
    temporal.take_snapshot(db_session, subject)

    db_session.add(Entity(name="Newcomer Ltd", type="company",
                          canonical_key="company:newcomer ltd", mention_count=2,
                          first_seen=datetime.utcnow(), last_seen=datetime.utcnow()))
    db_session.commit()
    second = temporal.take_snapshot(db_session, subject)

    assert "company:newcomer ltd" in second.delta["new_entities"]


def test_sentiment_shift_is_measured_as_share_not_count(db_session):
    """Counts mislead when volume changes; shares don't."""
    before = {"negative": 10, "neutral": 10}
    after = {"negative": 20, "neutral": 60}  # more negatives, smaller share
    shift = temporal._sentiment_shift(before, after)

    assert shift["before"] == 50.0
    assert shift["after"] == 25.0
    assert shift["change"] == -25.0


def test_summary_covers_the_horizons_an_analyst_uses(db_session):
    subject = _subject(db_session)
    _mention(db_session, subject, "recent", "a", "t3", hours_ago=2)
    db_session.commit()

    summary = temporal.temporal_summary(db_session, subject)

    assert summary["last_24h"]["mentions"] == 1
    for horizon in ("last_24h", "last_7d", "last_30d", "since_last_run"):
        assert horizon in summary


# --- anomalies -------------------------------------------------------------

def test_brand_new_name_is_surfaced(db_session):
    """The earliest possible signal: a name that wasn't there before."""
    subject = _subject(db_session)
    db_session.add(Entity(name="Opaque Holdings", type="company",
                          canonical_key="company:opaque holdings", mention_count=1,
                          first_seen=datetime.utcnow(), last_seen=datetime.utcnow()))
    db_session.commit()

    signals = anomaly.detect_first_appearances(db_session, subject)

    assert any("Opaque Holdings" in s["headline"] for s in signals)


def test_uncorroborated_story_is_flagged_neutrally(db_session):
    """An exclusive and a fabrication look identical here — say so, don't judge."""
    subject = _subject(db_session)
    db_session.add(Event(politician_id=subject.id, title="Secret deal signed",
                         dedupe_key="d1", independent_domains=1,
                         first_seen=datetime.utcnow()))
    db_session.commit()

    signals = anomaly.detect_single_source_claims(db_session, subject)

    assert len(signals) == 1
    detail = signals[0]["detail"].lower()
    assert "exclusive" in detail and "may not hold up" in detail


def test_copy_paste_campaign_is_detected(db_session):
    """Organic reaction varies in wording; identical text across accounts doesn't."""
    subject = _subject(db_session)
    slogan = "This leader has completely failed the people of this country entirely"
    for i in range(6):
        _mention(db_session, subject, slogan, f"account{i}", f"c{i}")
    db_session.commit()

    signals = anomaly.detect_coordinated_messaging(db_session, subject)

    assert signals, "identical posting across many accounts must be surfaced"
    assert len(signals[0]["accounts"]) >= 4


def test_organic_variation_is_not_flagged_as_coordination(db_session):
    subject = _subject(db_session)
    for i, text in enumerate([
        "I think the budget speech was quite reasonable overall today",
        "Completely disagree with this new taxation policy honestly",
        "The infrastructure plans look promising for our county",
        "Not sure about these numbers, they seem optimistic to me",
        "Where is the money for hospitals in this whole budget",
    ]):
        _mention(db_session, subject, text, f"person{i}", f"o{i}")
    db_session.commit()

    assert anomaly.detect_coordinated_messaging(db_session, subject) == []


def test_new_typed_relationship_is_surfaced(db_session):
    subject = _subject(db_session)
    a = Entity(name="Jane Doe", type="person", canonical_key="person:jane doe",
               first_seen=datetime.utcnow())
    b = Entity(name="Acme Ltd", type="company", canonical_key="company:acme ltd",
               first_seen=datetime.utcnow())
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(EntityRelationship(
        politician_id=subject.id, source_entity_id=a.id, target_entity_id=b.id,
        rel_type="owns", confidence=0.8, evidence_count=2,
        first_seen=datetime.utcnow(), last_seen=datetime.utcnow(),
    ))
    db_session.commit()

    signals = anomaly.detect_unexpected_relationships(db_session, subject)

    assert any("owns" in s["headline"] for s in signals)


def test_unusual_silence_is_itself_a_signal(db_session):
    """Absence never ranks by volume, so it has to be looked for."""
    subject = _subject(db_session)
    for i in range(60):  # a busy baseline month
        _mention(db_session, subject, f"routine coverage {i}", "outlet", f"b{i}",
                 hours_ago=24 * 20)
    db_session.commit()

    signals = anomaly.detect_quiet_periods(db_session, subject)

    assert signals and signals[0]["kind"] == "quiet_period"


def test_one_failing_detector_does_not_blind_the_others(db_session, monkeypatch):
    subject = _subject(db_session)
    db_session.add(Entity(name="Newcomer", type="person", canonical_key="person:newcomer",
                          mention_count=1, first_seen=datetime.utcnow()))
    db_session.commit()

    def boom(*a, **k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(anomaly, "detect_coordinated_messaging", boom)
    result = anomaly.detect_all(db_session, subject)

    assert result["count"] >= 1, "other detectors must still report"
    assert "coordinated_messaging" in result.get("detectors_failed", [])


def test_signals_are_ordered_by_attention_deserved(db_session):
    subject = _subject(db_session)
    db_session.add(Event(politician_id=subject.id, title="Single sourced thing",
                         dedupe_key="d2", independent_domains=1, first_seen=datetime.utcnow()))
    db_session.add(Entity(name="Minor Name", type="person", canonical_key="person:minor name",
                          mention_count=1, first_seen=datetime.utcnow()))
    db_session.commit()

    result = anomaly.detect_all(db_session, subject)
    severities = [s["severity"] for s in result["signals"]]
    assert severities == sorted(severities, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s])
