"""Entity & event resolution.

The property that matters: many reports of one happening must become ONE event
with many pieces of evidence — otherwise repetition masquerades as significance
and the loudest story outranks the best-corroborated one. Confidence therefore
tracks INDEPENDENT sources, not copies.
"""

from datetime import datetime

from engine.agents import resolve
from engine.agents.resolve import canonical_key, event_dedupe_key
from engine.db.models import Document, Entity, Event, EventEvidence, Politician


def _subject(db_session, name="Resolve Probe"):
    p = Politician(name=name, aliases=[], titles=[], keywords=[], swahili_terms=[])
    db_session.add(p)
    db_session.flush()
    return p


def _doc(db_session, subject, domain, h, body="text"):
    d = Document(
        politician_id=subject.id, title="t", body=body, content_hash=h,
        domain=domain, url=f"https://{domain}/{h}", source="searxng",
        fetched_at=datetime.utcnow(),
    )
    db_session.add(d)
    db_session.flush()
    return d


def _item(doc):
    return {"id": doc.id, "source_type": "article", "source": doc.domain,
            "text": doc.body, "posted_at": datetime(2026, 5, 3)}


# --- identity keys ---------------------------------------------------------

def test_entity_key_is_stable_across_spellings():
    assert canonical_key("John  Mbadi", "person") == canonical_key("john mbadi", "person")
    assert canonical_key("Acme, Ltd.", "company") == canonical_key("Acme Ltd", "company")
    # Different types are different entities even with the same name.
    assert canonical_key("Nairobi", "location") != canonical_key("Nairobi", "organization")


def test_event_key_matches_differently_worded_headlines():
    """Outlets word headlines differently but agree on the substantive nouns."""
    day = datetime(2026, 5, 3)
    a = event_dedupe_key("Ministry awards Mombasa terminal contract", day)
    b = event_dedupe_key("The Mombasa terminal contract was awarded by ministry", day)
    assert a == b

    # A genuinely different happening must not collapse into it.
    c = event_dedupe_key("Ministry cancels Kisumu railway tender", day)
    assert c != a


def test_event_key_separates_same_story_on_different_days():
    title = "Ministry awards Mombasa terminal contract"
    assert event_dedupe_key(title, datetime(2026, 5, 3)) != event_dedupe_key(title, datetime(2026, 6, 3))


# --- resolution ------------------------------------------------------------

def test_many_reports_of_one_happening_become_one_event(db_session, monkeypatch):
    """The core promise: 3 outlets reporting one contract award = 1 event,
    3 evidence rows, 3 independent domains."""
    subject = _subject(db_session)
    docs = [_doc(db_session, subject, d, f"h{i}") for i, d in enumerate(
        ["nation.example", "standard.example", "star.example"])]
    db_session.commit()

    monkeypatch.setattr(
        resolve, "_extract_batch",
        lambda subj, items: {
            i: {"entities": [{"name": "Ministry of Transport", "type": "organization", "relation": "awarded"}],
                "events": [{"title": "Ministry awards Mombasa terminal contract",
                            "date": "2026-05-03", "type": "contract", "summary": "s"}]}
            for i in range(len(items))
        },
    )

    stats = resolve.resolve_corpus(db_session, subject, [_item(d) for d in docs])

    events = db_session.query(Event).filter_by(politician_id=subject.id).all()
    assert len(events) == 1, "one happening, not three"
    assert events[0].corroboration_count == 3
    assert events[0].independent_domains == 3
    assert events[0].confidence >= 0.9
    assert stats["events"] == 1


def test_syndicated_copies_do_not_inflate_confidence(db_session, monkeypatch):
    """Ten copies of one wire story are one source — confidence must say so."""
    subject = _subject(db_session)
    docs = [_doc(db_session, subject, "wire.example", f"w{i}") for i in range(10)]
    db_session.commit()

    monkeypatch.setattr(
        resolve, "_extract_batch",
        lambda subj, items: {
            i: {"entities": [], "events": [{"title": "Contract awarded to firm",
                                            "date": "2026-05-03", "type": "contract", "summary": "s"}]}
            for i in range(len(items))
        },
    )
    resolve.resolve_corpus(db_session, subject, [_item(d) for d in docs])

    event = db_session.query(Event).filter_by(politician_id=subject.id).one()
    assert event.corroboration_count == 10
    assert event.independent_domains == 1, "one outlet, however many reprints"
    assert event.confidence <= 0.5, "single-sourced must not read as well-corroborated"


def test_entities_track_first_and_last_seen(db_session, monkeypatch):
    """First appearance is a signal in its own right, so it must be recorded."""
    subject = _subject(db_session)
    doc = _doc(db_session, subject, "news.example", "e1")
    db_session.commit()

    monkeypatch.setattr(
        resolve, "_extract_batch",
        lambda subj, items: {
            0: {"entities": [{"name": "Acme Holdings", "type": "company", "relation": "contractor"}],
                "events": []}
        },
    )
    resolve.resolve_corpus(db_session, subject, [_item(doc)])

    entity = db_session.query(Entity).filter_by(canonical_key="company:acme holdings").one()
    assert entity.first_seen is not None
    assert entity.last_seen is not None
    assert entity.mention_count == 1


def test_rerun_enriches_rather_than_duplicating(db_session, monkeypatch):
    subject = _subject(db_session)
    doc = _doc(db_session, subject, "news.example", "r1")
    db_session.commit()
    monkeypatch.setattr(
        resolve, "_extract_batch",
        lambda subj, items: {
            0: {"entities": [{"name": "Acme Holdings", "type": "company", "relation": "x"}],
                "events": [{"title": "Acme wins tender", "date": "2026-05-03",
                            "type": "contract", "summary": "s"}]}
        },
    )

    resolve.resolve_corpus(db_session, subject, [_item(doc)])
    resolve.resolve_corpus(db_session, subject, [_item(doc)])

    assert db_session.query(Event).filter_by(politician_id=subject.id).count() == 1
    assert db_session.query(Entity).filter_by(canonical_key="company:acme holdings").count() == 1
    # The same document must not be counted twice as corroboration.
    assert db_session.query(EventEvidence).count() == 1


def test_extraction_failure_is_survivable(db_session, monkeypatch):
    subject = _subject(db_session)
    doc = _doc(db_session, subject, "news.example", "f1")
    db_session.commit()
    monkeypatch.setattr(resolve, "_extract_batch", lambda subj, items: {})

    stats = resolve.resolve_corpus(db_session, subject, [_item(doc)])
    assert stats["events"] == 0 and stats["entities"] == 0


def test_empty_corpus_is_a_no_op(db_session, monkeypatch):
    subject = _subject(db_session)

    def fail(*a, **k):
        raise AssertionError("must not call the model with nothing to resolve")

    monkeypatch.setattr(resolve, "_extract_batch", fail)
    assert resolve.resolve_corpus(db_session, subject, [])["items"] == 0
