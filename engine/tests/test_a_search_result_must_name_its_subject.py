"""A search returning an item is a reason to look at it, not proof it is about the subject.

Every GDELT / Google News / YouTube / Reddit / X result used to be linked to
its subject unread. A client's report then carried Russian newspapers; the
relevance probe (scripts/relevance_probe.py, 11 Nov 2026) measured a GDELT
query for "Ministry of Health" returning 106 items in German, Chinese,
Portuguese and Russian — none about Kenya — and 54% of GDELT headlines for
the head of state never naming him. processing/subject_match.py decides on
the item's own words, with three answers: about them, not (with a reason),
and unverified (a headline over an unread article, judged again when read).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.processing.subject_match import Gate, is_generic_name


def item(title, source="gdelt", kind="article", body=None, **raw):
    r = {"source": source, "title": title, **raw}
    text = title
    if body:
        r["article_text"] = body
        text = f"{title}\n\n{body}"
    return {"text": text, "source_type": kind, "platform": raw.get("outlet_domain") or "news",
            "author_handle": "x", "raw_payload": r}


RUTO = Gate("William Ruto", ["William Ruto", "Hon. William Ruto"], "politician")
MOH = Gate("Ministry of Health", ["Ministry of Health"], "ministry")


def test_an_article_in_a_language_the_engine_does_not_read_is_set_aside():
    v = MOH.judge(item("Думают , что бессмертные . Кто рискует заработать инфаркт", language="Russian"))
    assert not v.matched and v.final and "Russian" in v.reason
    v = MOH.judge(item("整頓身障就業市場 日本擬推 「 登記制 」 防堵亂象"))
    assert not v.matched and "script" in v.reason


@pytest.mark.parametrize("title", ["Why Ruto wants a second term", "President Ruto's allies regroup",
                                   "Kenyans Tell Ruto To Resign Over Taxes", "Kindiki, Ruto tour Meru"])
def test_the_short_name_the_press_prints_counts_for_a_name_search(title):
    assert RUTO.judge(item(title)).matched


@pytest.mark.parametrize("title", ["Charlene Ruto launches youth programme",
                                   "Rachel Ruto leads prayers at State House"])
def test_another_member_of_the_family_is_not_the_subject(title):
    assert not RUTO.judge(item(title)).matched


def test_a_place_named_for_a_person_is_not_the_person():
    raila = Gate("Raila Odinga", ["Raila Odinga"], "politician")
    assert not raila.judge(item("Bayern Munich, Ajax to headline opening of Raila Odinga Stadium")).matched


def test_a_headline_that_does_not_name_them_is_unverified_until_the_article_is_read():
    v = RUTO.judge(item("NHC has delivered more than 50,000 homes"))
    assert not v.matched and not v.final
    once = "The awards were presented at State House. " * 12
    read = RUTO.judge(item("Nick Kwach reflects on career", body=once + " President William Ruto presided. " + once))
    assert not read.matched and read.final and "passing" in read.reason
    lede = RUTO.judge(item("Tax plan stays", body="President William Ruto on Monday said the plan stays. " + "y " * 300))
    assert lede.matched


def test_a_generic_institution_must_be_kenyas():
    assert is_generic_name("Ministry of Health") and is_generic_name("National Treasury")
    assert not is_generic_name("Central Bank of Kenya") and not is_generic_name("Safaricom")
    assert MOH.judge(item("Ministry of Health confirms cholera outbreak", outlet_domain="nation.africa")).matched
    foreign = MOH.judge(item("Ministry of Health confirms cholera outbreak", outlet_domain="punchng.com",
                             source_country="Nigeria"))
    assert not foreign.matched and "Kenya" in foreign.reason


def test_a_primary_record_is_not_matched_on_a_surname():
    v = RUTO.judge({"text": "Ruto v Republic (Criminal Appeal 12 of 2020)", "source_type": "judgment",
                    "raw_payload": {"source": "kenya_law"}})
    assert not v.matched


def test_gdelt_is_asked_for_the_name_as_a_phrase_and_a_generic_name_with_kenya():
    from engine.ingestion import queries

    def subject(name, kind):
        return SimpleNamespace(name=name, aliases=[name], titles=[], swahili_terms=[], tracked_hashtags=[],
                               subject_type=kind, investigation_leads=None, keywords=[])

    ruto = queries.connector_variants(subject("William Ruto", "politician"), "gdelt")
    assert ruto[0] == '"William Ruto"'
    moh = queries.connector_variants(subject("Ministry of Health", "ministry"), "gdelt")
    assert all("Kenya" in q for q in moh)
    assert queries.connector_variants(subject("Safaricom", "business"), "google_news")[0] == "Safaricom"


def test_the_report_links_only_what_names_the_subject(db_session, monkeypatch):
    """The pipeline: a name-search result that does not name the subject is
    not linked, and a link an earlier run made on the old assumption is
    withdrawn."""
    from datetime import datetime, timedelta

    from engine import pipeline
    from engine.db.models import MentionEntity, Politician, RawMention

    subject = Politician(name="William Ruto", aliases=["William Ruto"], keywords=[], subject_type="politician")
    db_session.add(subject)
    db_session.flush()
    now = datetime.utcnow()

    def row(title, h, language="English"):
        m = RawMention(politician_id=subject.id, platform="news", source_type="article", author_handle="x",
                       text=title, posted_at=now - timedelta(days=2), engagement_json={},
                       raw_payload={"source": "gdelt", "title": title, "language": language},
                       content_hash=h, is_spam=0)
        db_session.add(m)
        return m

    good = row("Ruto signs the finance bill", "a")
    russian = row("Думают , что бессмертные", "b", language="Russian")
    db_session.flush()
    politician_entity = pipeline._upsert_entity(db_session, "politician", subject.name)
    db_session.add(MentionEntity(mention_id=russian.id, entity_id=politician_entity.id,
                                 confidence=0.75, match_type="targeted_gdelt"))
    db_session.commit()

    monkeypatch.setattr(pipeline.health, "preflight", lambda: {})
    monkeypatch.setattr(pipeline.entities, "extract_people_items", lambda *a, **k: {})
    seen = {}
    try:
        pipeline.run_analysis(db_session, subject, "test", now - timedelta(days=30), now,
                              on_section=lambda k, v: seen.__setitem__(k, v))
    except Exception:  # the rest of the run is not under test here
        pass
    linked = {m.mention_id for m in db_session.query(MentionEntity)
              .filter_by(entity_id=politician_entity.id).all()}
    assert good.id in linked
    assert russian.id not in linked
    sf = seen.get("subject_filter") or {}
    if sf:
        assert sf["earlier_links_withdrawn"] >= 1
