"""Deleting the accumulated corpus, safely.

The corpus compounds by design. That is right in production and wrong while
the pipeline is being rebuilt underneath it: every run shows material
collected by a version of the system that no longer exists, and the page
renders the last stored payload for a subject BEFORE the new run produces
anything — so old output appears instantly and reads as new.

This is irreversible, so the rails matter more than the feature.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from engine.admin import purge
from engine.db.models import IntelligenceReport, Politician, RawMention, RunProgress


def _seed(db, name="Edwin Sifuna", mentions=5):
    subject = Politician(name=name, aliases=[], titles=[], swahili_terms=[],
                         subject_type="politician")
    db.add(subject)
    db.flush()
    now = datetime.utcnow()
    for i in range(mentions):
        db.add(RawMention(politician_id=subject.id, platform="x", source_type="post",
                          author_handle=f"a{i}", text=f"{name} item {i}",
                          posted_at=now - timedelta(days=i), engagement_json={},
                          source_url=f"https://x/{name}/{i}", is_spam=0))
    db.add(IntelligenceReport(politician_id=subject.id, period="weekly",
                              window_start=now - timedelta(days=30), window_end=now,
                              payload={"executive_summary": "old"}))
    db.add(RunProgress(subject_key=name.lower(), kind="report", status="done",
                       payload={"executive_summary": "stale"}))
    db.commit()
    return subject


# --- the rails ---------------------------------------------------------------

def test_nothing_is_deleted_without_the_confirmation_phrase(db_session):
    _seed(db_session)
    with pytest.raises(ValueError) as caught:
        purge.purge_all(db_session, confirm="")
    assert "cannot be undone" in str(caught.value)
    assert db_session.query(RawMention).count() == 5, "rows went despite the refusal"


def test_a_wrong_confirmation_phrase_is_refused(db_session):
    _seed(db_session)
    with pytest.raises(ValueError):
        purge.purge_all(db_session, confirm="yes")
    assert db_session.query(Politician).count() == 1


def test_a_subject_purge_also_requires_confirmation(db_session):
    _seed(db_session)
    with pytest.raises(ValueError):
        purge.purge_subject(db_session, "Edwin Sifuna", confirm="please")
    assert db_session.query(RawMention).count() == 5


def test_counting_deletes_nothing(db_session):
    _seed(db_session)
    before = purge.counts(db_session)
    assert before["raw_mentions"] == 5
    assert purge.counts(db_session) == before


def test_the_migration_table_is_never_in_the_purge_list():
    """Dropping it makes the database unmigratable."""
    assert "alembic_version" not in purge.RUN_DATA_TABLES
    assert "alembic_version" in purge.NEVER_TOUCH


def test_spend_history_is_preserved_by_default():
    """llm_usage is accounting, not report data — a corpus reset must not
    destroy the only record of what the runs cost."""
    assert "llm_usage" not in purge.RUN_DATA_TABLES
    assert "llm_usage" in purge.PRESERVED_BY_DEFAULT


# --- purging everything ------------------------------------------------------

def test_purge_all_empties_the_corpus(db_session):
    _seed(db_session, "Edwin Sifuna")
    _seed(db_session, "Uhuru Kenyatta")
    result = purge.purge_all(db_session, confirm=purge.CONFIRMATION)

    assert db_session.query(Politician).count() == 0
    assert db_session.query(RawMention).count() == 0
    assert db_session.query(IntelligenceReport).count() == 0
    assert result["total_rows_deleted"] > 0


def test_purge_all_clears_the_progress_payload_the_page_renders_first(db_session):
    """This is the row that makes old output appear instantly on a new run."""
    _seed(db_session)
    purge.purge_all(db_session, confirm=purge.CONFIRMATION)
    assert db_session.query(RunProgress).count() == 0


def test_purge_all_reports_what_it_deleted(db_session):
    _seed(db_session, mentions=7)
    result = purge.purge_all(db_session, confirm=purge.CONFIRMATION)
    assert result["rows_deleted"]["raw_mentions"] == 7
    assert result["remaining"] == {}


def test_a_purged_database_is_still_usable(db_session):
    """TRUNCATE ... CASCADE on the wrong set can take the schema with it."""
    _seed(db_session)
    purge.purge_all(db_session, confirm=purge.CONFIRMATION)
    assert db_session.execute(text("SELECT count(*) FROM raw_mentions")).scalar() == 0
    subject = _seed(db_session, "New Subject")
    assert subject.id and db_session.query(RawMention).count() == 5


# --- purging one subject -----------------------------------------------------

def test_purging_one_subject_leaves_the_others(db_session):
    _seed(db_session, "Edwin Sifuna")
    _seed(db_session, "Uhuru Kenyatta")

    purge.purge_subject(db_session, "Sifuna", confirm=purge.CONFIRMATION)

    remaining = [p.name for p in db_session.query(Politician).all()]
    assert remaining == ["Uhuru Kenyatta"]
    assert db_session.query(RawMention).count() == 5, "the other subject's corpus went too"


def test_purging_one_subject_clears_its_stale_payload(db_session):
    _seed(db_session, "Edwin Sifuna")
    _seed(db_session, "Uhuru Kenyatta")
    purge.purge_subject(db_session, "Edwin Sifuna", confirm=purge.CONFIRMATION)
    keys = [r.subject_key for r in db_session.query(RunProgress).all()]
    assert keys == ["uhuru kenyatta"]


def test_purging_an_unknown_subject_is_not_an_error(db_session):
    _seed(db_session)
    result = purge.purge_subject(db_session, "Nobody At All", confirm=purge.CONFIRMATION)
    assert result["matched"] == []
    assert db_session.query(Politician).count() == 1, "an unmatched name deleted data"
