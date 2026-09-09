"""The report that died on a foreign key.

    ForeignKeyViolation: insert or update on table "event_evidence" violates
    constraint "event_evidence_document_id_fkey"
    DETAIL: Key (document_id)=(b790c943-...) is not present in table "documents".
    ... PendingRollbackError: This Session's transaction has been rolled back

Two defects, and the second is worse than the first.

`_link_evidence` decided which table a corpus id belonged to by asking
`source_type == "article"`. That is not the same question. GDELT stores news
as RawMention rows whose source_type is ALSO "article", so a mention's id was
written into event_evidence.document_id and Postgres refused the flush.

The bug had been there all along and only fired once GDELT started delivering
again — the pacing fix in the previous commit is what let the news through.

Then the guard around resolution caught the exception and did not roll back.
SQLAlchemy will not let a session continue after a failed flush, so every
later statement raised PendingRollbackError naming the ORIGINAL error. The
handler's own comment says "resolution must never break a report"; that was
true of the exception and false of its wreckage, and the whole run died with
"report generation failed" instead of one absent section.
"""

import pytest

from engine.agents import resolve
from datetime import datetime

from engine.db.models import Document, Event, Politician, RawMention


def test_a_news_mention_is_not_written_as_a_document(db_session):
    """A RawMention with source_type "article" — exactly what GDELT stores."""
    subject = Politician(name="FK Probe")
    db_session.add(subject)
    db_session.flush()
    mention = RawMention(
        politician_id=subject.id, platform="nation.africa", source_type="article",
        author_handle="nation.africa", text="Kalonzo launches his platform",
        content_hash="fk-probe-1", is_spam=0, posted_at=datetime.utcnow(),
    )
    event = Event(politician_id=subject.id, title="Launch", event_type="intersection")
    db_session.add_all([mention, event])
    db_session.flush()

    linked = resolve._link_evidence(
        db_session, event,
        {"id": mention.id, "kind": "mention", "source_type": "article", "text": "t"})
    assert linked
    db_session.flush()   # the flush that used to raise ForeignKeyViolation

    row = db_session.query(resolve.EventEvidence).filter_by(event_id=event.id).one()
    assert row.mention_id == mention.id
    assert row.document_id is None, "a mention was recorded as a document"


def test_a_real_document_is_still_written_as_one(db_session):
    subject = Politician(name="FK Probe Doc")
    db_session.add(subject)
    db_session.flush()
    doc = Document(
        politician_id=subject.id, url="http://news.example/a", domain="news.example",
        title="A story", body="Body text.", source="searxng", content_hash="fk-doc-1",
    )
    event = Event(politician_id=subject.id, title="Story", event_type="intersection")
    db_session.add_all([doc, event])
    db_session.flush()

    resolve._link_evidence(
        db_session, event,
        {"id": doc.id, "kind": "document", "source_type": "article", "text": "t"})
    db_session.flush()

    row = db_session.query(resolve.EventEvidence).filter_by(event_id=event.id).one()
    assert row.document_id == doc.id
    assert row.mention_id is None


def test_an_unmarked_row_is_looked_up_rather_than_guessed(db_session):
    """Older callers do not set `kind`. Guessing is what caused this; asking
    the database is cheap and correct."""
    subject = Politician(name="FK Probe Legacy")
    db_session.add(subject)
    db_session.flush()
    mention = RawMention(
        politician_id=subject.id, platform="gdelt", source_type="article",
        author_handle="gdelt", text="text", content_hash="fk-legacy-1", is_spam=0,
        posted_at=datetime.utcnow(),
    )
    event = Event(politician_id=subject.id, title="Legacy", event_type="intersection")
    db_session.add_all([mention, event])
    db_session.flush()

    resolve._link_evidence(
        db_session, event,
        {"id": mention.id, "source_type": "article", "text": "t"})  # no "kind"
    db_session.flush()

    row = db_session.query(resolve.EventEvidence).filter_by(event_id=event.id).one()
    assert row.document_id is None


def test_every_corpus_builder_states_which_table_it_read():
    """The inference existed because nothing said. Each builder now does, so a
    consumer never has to guess again."""
    import inspect

    from engine import pipeline
    from engine.agents import evidence

    for source in (inspect.getsource(pipeline._document_corpus),
                   inspect.getsource(pipeline._as_corpus_dicts)):
        assert '"kind":' in source

    evidence_source = inspect.getsource(evidence)
    assert evidence_source.count('"kind":') >= 4


# --- the wreckage, not just the exception ------------------------------------

def test_a_failed_stage_leaves_the_session_usable(db_session):
    """Catching the exception is not containing the failure: the session stays
    poisoned and the NEXT stage is the one that dies."""
    from engine import pipeline

    subject = Politician(name="Rollback Probe")
    db_session.add(subject)
    db_session.commit()

    # Poison the session the way a bad flush does.
    db_session.add(RawMention(politician_id=None, platform="x", source_type="post",
                              author_handle="x", text="t", content_hash="poison-1",
                              is_spam=0, posted_at=datetime.utcnow()))
    with pytest.raises(Exception):
        db_session.flush()

    pipeline._rollback_quietly(db_session)

    # The session must now serve an ordinary query rather than raising
    # PendingRollbackError about the original failure.
    assert db_session.query(Politician).filter_by(name="Rollback Probe").one()


def test_the_guards_that_wrap_database_work_roll_back():
    import inspect

    from engine import pipeline

    source = inspect.getsource(pipeline.run_analysis)
    assert source.count("_rollback_quietly(db)") >= 2
