"""A fact is kept only if the source printed the words it rests on.

Phase 2 of docs/FOUNDATION.md, and the founder's standard: "If we say that
this was said, or Martha Karua did this or that, we want it to be legit
data." The extractor existed (`evidence/records.py`) and asked for a
verbatim quote, but nothing checked it, nothing stored the result, and it
read the first 1,800 characters of each source — a headline and a lede.

Each test pins one way the store could hold something a source never said.
"""

from datetime import datetime

import pytest

from engine import llm
from engine.config import settings
from engine.db.models import Fact, FactExtraction, Politician, RawMention
from engine.evidence import facts

ARTICLE = ("The Senate Finance Committee on Tuesday summoned the Treasury over the "
           "delayed county disbursements. Committee chair Jane Wanjiru said the "
           "delay had left fourteen counties unable to pay health workers. "
           + "Background paragraph about earlier budgets. " * 60
           + "Late in the article, the Treasury said it would release the funds "
             "within ten days of the committee hearing.")


# --- the check itself --------------------------------------------------------

def test_the_source_s_own_words_pass_through_typographic_noise():
    assert facts.quote_in_source(
        "the delay had left fourteen counties unable to pay health workers",
        ARTICLE)
    assert facts.quote_in_source("“The Senate Finance Committee on Tuesday summoned”",
                                 ARTICLE.replace("The Senate", "The  Senate"))


def test_a_paraphrase_is_not_a_quote():
    assert not facts.quote_in_source(
        "the delay left 14 counties unable to pay their health workers", ARTICLE)


def test_an_ellipsis_joins_pieces_that_must_both_be_there_in_order():
    assert facts.quote_in_source(
        "Committee chair Jane Wanjiru said … fourteen counties unable to pay", ARTICLE)
    assert not facts.quote_in_source(
        "fourteen counties unable to pay … Committee chair Jane Wanjiru said", ARTICLE)


def test_a_few_common_words_prove_nothing():
    assert not facts.quote_in_source("the Treasury said", ARTICLE)


# --- the store ---------------------------------------------------------------

def _subject_and_item(db_session, text=ARTICLE):
    subject = Politician(name="Fact Probe")
    db_session.add(subject)
    db_session.flush()
    mention = RawMention(politician_id=subject.id, platform="nation.africa",
                         source_type="article", author_handle="Daily Nation", text=text,
                         posted_at=datetime(2026, 10, 20), engagement_json={},
                         raw_payload={}, content_hash=f"fact-probe-{len(text)}", is_spam=0)
    db_session.add(mention)
    db_session.commit()
    item = {"id": mention.id, "kind": "mention", "platform": "nation.africa",
            "author_handle": "Daily Nation", "text": text, "full_text": True,
            "posted_at": datetime(2026, 10, 20), "date_estimated": False}
    return subject, mention, item


def _model(monkeypatch, records=None, error=None):
    calls = []

    def answer(instructions, untrusted, expected_keys, **kw):
        calls.append(untrusted)
        if error:
            raise error
        return {"records": records or []}

    monkeypatch.setattr(llm, "call_json_untrusted", answer)
    return calls


GOOD = {"i": 1, "kind": "fact", "status": "reported",
        "statement": "Jane Wanjiru said the delay left fourteen counties unable to pay health workers.",
        "quote": "the delay had left fourteen counties unable to pay health workers",
        "actor": "Jane Wanjiru", "topic": "county disbursements", "sentiment": "neutral"}
INVENTED = {"i": 1, "kind": "fact", "status": "reported",
            "statement": "The Treasury admitted it diverted the county funds.",
            "quote": "the Treasury admitted it had diverted the county funds",
            "actor": "Treasury", "topic": "county funds", "sentiment": "negative"}


def test_only_what_the_source_printed_is_kept_and_the_rest_is_counted(db_session, monkeypatch):
    subject, mention, item = _subject_and_item(db_session)
    _model(monkeypatch, [GOOD, INVENTED])
    stats = facts.extract_and_store(db_session, subject, [item])

    stored = db_session.query(Fact).all()
    assert [f.statement for f in stored] == [GOOD["statement"]]
    assert stored[0].mention_id == mention.id and stored[0].quote == GOOD["quote"]
    assert stats["facts"] == 1 and stats["dropped_unverified"] == 1
    read = db_session.query(FactExtraction).one()
    assert (read.status, read.facts, read.dropped_unverified) == ("read", 1, 1)


def test_the_whole_article_is_read_not_its_first_paragraph(db_session, monkeypatch):
    subject, _, item = _subject_and_item(db_session)
    late = {**GOOD, "statement": "The Treasury said it would release the funds within ten days.",
            "quote": "the Treasury said it would release the funds within ten days"}
    calls = _model(monkeypatch, [late])
    facts.extract_and_store(db_session, subject, [item])
    assert ARTICLE.index("Late in the article") > 1800
    assert "release the funds within ten days" in calls[0]
    assert db_session.query(Fact).count() == 1


def test_an_allegation_is_not_promoted(db_session, monkeypatch):
    subject, _, item = _subject_and_item(db_session)
    _model(monkeypatch, [{**GOOD, "kind": "fact", "status": "alleged"}])
    facts.extract_and_store(db_session, subject, [item])
    assert db_session.query(Fact).one().kind == "claim"


def test_a_failed_read_is_not_an_empty_source_and_is_tried_again(db_session, monkeypatch):
    subject, _, item = _subject_and_item(db_session)
    _model(monkeypatch, error=RuntimeError("provider 503"))
    stats = facts.extract_and_store(db_session, subject, [item])
    read = db_session.query(FactExtraction).one()
    assert read.status == "failed" and "provider 503" in read.error
    assert stats["failed"] == 1 and stats["read"] == 0

    _model(monkeypatch, [GOOD])
    facts.extract_and_store(db_session, subject, [item])
    db_session.expire_all()
    assert db_session.query(FactExtraction).one().status == "read"
    assert db_session.query(Fact).count() == 1


def test_a_source_is_read_once(db_session, monkeypatch):
    subject, _, item = _subject_and_item(db_session)
    calls = _model(monkeypatch, [GOOD])
    facts.extract_and_store(db_session, subject, [item])
    facts.extract_and_store(db_session, subject, [item])
    assert len(calls) == 1, "a source already read was sent to the model again"


def test_an_estimated_date_is_not_given_to_the_fact(db_session, monkeypatch):
    subject, _, item = _subject_and_item(db_session)
    item["date_estimated"] = True
    _model(monkeypatch, [GOOD])
    facts.extract_and_store(db_session, subject, [item])
    assert db_session.query(Fact).one().published_at is None


def test_the_back_office_fills_the_store_after_delivery(db_session, monkeypatch):
    from engine import health, stages
    from engine.pipeline import BackOfficeTask, run_back_office

    subject, _, item = _subject_and_item(db_session)
    _model(monkeypatch, [GOOD])
    monkeypatch.setattr(settings, "enable_resolution", False)
    monkeypatch.setattr(settings, "enable_verification", False)
    task = BackOfficeTask(report_id="00000000-0000-0000-0000-000000000000", politician_id=subject.id, corpus=[item],
                          payload={}, ingestion_run_id=None,
                          ledger=stages.StageLedger(), tracker=health.RunHealth())
    run_back_office(task, session=db_session)
    assert task.payload["facts"]["facts"] == 1
    assert db_session.query(Fact).count() == 1


@pytest.mark.parametrize("key", ["", None])
def test_an_item_whose_table_is_not_stated_is_not_guessed(key):
    assert facts.source_key({"id": "x", "kind": key}) is None
