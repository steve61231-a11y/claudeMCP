from datetime import datetime, timedelta

from engine.db.models import MentionSentiment, RawMention
from engine.intelligence import alerts as alerts_module
from engine.intelligence.alerts import detect_alerts


def _mention(db, politician, text, posted_at, author="cit1", sentiment=None, engagement=None, platform="tiktok"):
    m = RawMention(
        politician_id=politician.id,
        platform=platform,
        source_type="post",
        author_handle=author,
        text=text,
        posted_at=posted_at,
        engagement_json=engagement or {},
        raw_payload={},
        content_hash=f"h-{author}-{posted_at.isoformat()}-{text[:12]}",
        is_spam=0,
    )
    db.add(m)
    db.flush()
    if sentiment:
        db.add(MentionSentiment(mention_id=m.id, sentiment=sentiment, intensity=0.5, confidence=0.9, source="test"))
    return m


def _politician(db):
    from engine.db.models import Politician

    p = Politician(name="Test Pol", aliases=[], keywords=[])
    db.add(p)
    db.flush()
    return p


def test_negative_spike_and_new_amplifier(db_session, monkeypatch):
    monkeypatch.setattr(alerts_module.settings, "alert_webhook_url", "")
    db = db_session
    p = _politician(db)
    now = datetime(2026, 7, 1, 12)

    # Baseline week: 10 mentions, mostly neutral, from regular authors.
    for i in range(10):
        _mention(db, p, f"baseline post {i}", now - timedelta(days=3, hours=i), author=f"old{i % 3}",
                 sentiment="neutral" if i < 9 else "negative")
    # Recent 24h: 6 negative mentions, 3 from a brand-new account.
    for i in range(6):
        _mention(db, p, f"angry post {i}", now - timedelta(hours=i + 1),
                 author="newvoice" if i < 3 else f"old{i}", sentiment="negative")
    db.commit()

    raised = detect_alerts(db, p, now=now)
    kinds = {a.kind for a in raised}
    assert "negative_spike" in kinds
    assert "new_amplifier" in kinds
    amp = next(a for a in raised if a.kind == "new_amplifier")
    assert "newvoice" in amp.headline

    # Dedupe: immediate re-run raises nothing new.
    assert detect_alerts(db, p, now=now) == []


def test_viral_post_severity_follows_sentiment(db_session, monkeypatch):
    monkeypatch.setattr(alerts_module.settings, "alert_webhook_url", "")
    db = db_session
    p = _politician(db)
    now = datetime(2026, 7, 1, 12)
    _mention(db, p, "huge negative video", now - timedelta(hours=2), author="viralguy",
             sentiment="negative", engagement={"views": 90000})
    db.commit()

    raised = detect_alerts(db, p, now=now)
    viral = [a for a in raised if a.kind == "viral_post"]
    assert viral and viral[0].severity == "critical"
    assert viral[0].evidence and viral[0].evidence[0]["author"] == "viralguy"
