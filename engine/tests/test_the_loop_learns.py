""""The more it keeps running these reports, the better it gets."

Every piece of this was already here and not joined up.
`IngestionTask` records, for every run, the connector, the exact query, and
`result_count` — and because the store is `ON CONFLICT DO NOTHING` on a
content hash, that count is NEW, non-duplicate mentions. A query that
returns only what other queries already found scores zero, which is exactly
the right signal: it taught us nothing.

Nobody had ever read those rows back. Every run asked a blind rotating slice
of the same probe list, so run twenty asked the same eighteen questions that
had come back empty nineteen times, while the two that always work competed
for the same slots as the rest.

Four properties, and three of them are about what the loop must NOT do —
which is the harder half:

- it repeats what worked;
- it keeps asking questions it has never asked, and that share cannot be
  competed away, because a loop that only exploits stops discovering and the
  subject it stops discovering about is the one whose record has not
  surfaced yet;
- it sets aside answered questions only temporarily, because a subject with
  no court record this year may have one next year;
- it never treats a FAILED task as an empty result, because a source being
  down is not a question being answered — this codebase's founding rule,
  reached through a query planner.
"""

from datetime import datetime, timedelta

import pytest

from engine.config import settings
from engine.db.models import IngestionRun, IngestionTask, Politician
from engine.ingestion import learning, orchestrator, queries

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59))


def _subject(db, name="Musalia Mudavadi"):
    politician = Politician(
        name=name, aliases=["Mudavadi"], keywords=["Foreign Affairs"],
        titles=["Prime Cabinet Secretary"], swahili_terms=[],
        tracked_hashtags=[], social_handles={},
    )
    db.add(politician)
    db.commit()
    return politician


#: Past runs need DISTINCT timestamps, like real ones do: `recall` orders by
#: `started_at`, and two runs stamped the same second are not orderable, so a
#: test that gave them all one timestamp was asserting against whatever order
#: the database happened to return.
_clock = {"n": 0}


def _past_run(db, politician, results: dict[str, int], source="google_news",
              when=None, status="done"):
    """A finished run that asked `results`' queries and got those counts."""
    if when is None:
        _clock["n"] += 1
        when = datetime(2026, 1, 1) + timedelta(hours=_clock["n"])
    run = IngestionRun(politician_id=politician.id,
                       window_start=WINDOW[0], window_end=WINDOW[1],
                       status="completed",
                       started_at=when)
    db.add(run)
    db.flush()
    for query, count in results.items():
        db.add(IngestionTask(run_id=run.id, connector=source, platform="news",
                             endpoint="rss", query=query, status=status,
                             result_count=count))
    db.commit()
    return run


# --- the memory ---------------------------------------------------------------

def test_what_a_query_returned_is_remembered(db_session):
    politician = _subject(db_session)
    _past_run(db_session, politician, {"worked": 14, "empty": 0})

    memory = learning.recall(db_session, politician, "google_news")
    assert memory["worked"]["new_mentions"] == 14
    assert memory["worked"]["average"] == 14.0
    assert memory["empty"]["new_mentions"] == 0


def test_a_failed_task_is_not_an_empty_result(db_session):
    """A source being down is not a question being answered. Counting it as
    one would retire a perfectly good query because archive.org had a bad
    afternoon — the one confusion this whole codebase exists to prevent."""
    politician = _subject(db_session)
    for _ in range(3):
        _past_run(db_session, politician, {"courts": 0}, status="failed")

    memory = learning.recall(db_session, politician, "google_news")
    assert "courts" not in memory, (
        "a failed task was recorded as a query that found nothing")


def test_two_empty_results_set_a_question_aside(db_session):
    politician = _subject(db_session)
    for _ in range(2):
        _past_run(db_session, politician, {"courts": 0, "contracts": 5})

    memory = learning.recall(db_session, politician, "google_news")
    assert memory["courts"]["retired"] is True
    assert memory["contracts"]["retired"] is False


def test_one_empty_result_is_noise_not_an_answer(db_session):
    politician = _subject(db_session)
    _past_run(db_session, politician, {"courts": 0})

    memory = learning.recall(db_session, politician, "google_news")
    assert memory["courts"]["retired"] is False, (
        "a single quiet window retired a question")


def test_a_retirement_lifts_so_the_question_is_asked_again(db_session):
    """The world moves. A memory that retires a question permanently
    guarantees we never find out that the answer changed."""
    politician = _subject(db_session)
    for _ in range(2):
        _past_run(db_session, politician, {"courts": 0})
    # Then a long run of other work, pushing the empties out of reach.
    for index in range(learning.RETIREMENT_RUNS + 1):
        _past_run(db_session, politician, {f"other-{index}": 3})

    memory = learning.recall(db_session, politician, "google_news")
    assert memory["courts"]["retired"] is False, (
        f"a question is still set aside {learning.RETIREMENT_RUNS}+ runs later")


# --- the plan -----------------------------------------------------------------

CANDIDATES = ["name", "worked-a", "worked-b", "empty-a", "empty-b", "fresh-a",
              "fresh-b", "fresh-c"]


def test_what_worked_is_repeated():
    memory = {"worked-a": {"new_mentions": 20, "average": 20.0, "retired": False,
                           "asked": 1, "empty_in_a_row": 0},
              "worked-b": {"new_mentions": 4, "average": 4.0, "retired": False,
                           "asked": 1, "empty_in_a_row": 0}}
    out = learning.plan(CANDIDATES, memory, budget=6, always_first="name")
    assert out["queries"][0] == "name", "the bare name stopped being first"
    assert "worked-a" in out["queries"]
    # Best first, so a truncating source gets the strongest query.
    assert out["queries"].index("worked-a") < out["queries"].index("worked-b")


def test_a_share_of_every_budget_goes_to_questions_never_asked():
    """The property that stops this narrowing into a monoculture. Give it
    eight proven queries and it still has to spend half the budget on
    something it has never tried."""
    memory = {q: {"new_mentions": 50, "average": 50.0, "retired": False,
                  "asked": 3, "empty_in_a_row": 0}
              for q in CANDIDATES if q.startswith("worked") or q.startswith("empty")}
    out = learning.plan(CANDIDATES, memory, budget=6, always_first="name")
    assert out["explored"], (
        "every slot went to a remembered query — the loop has stopped "
        "discovering anything")
    assert len(out["proven"]) <= 3, (
        f"{len(out['proven'])} of a 6 budget went to proven queries, past "
        "the exploit share")


def test_a_set_aside_question_does_not_run():
    memory = {"empty-a": {"new_mentions": 0, "average": 0.0, "retired": True,
                          "asked": 2, "empty_in_a_row": 2}}
    out = learning.plan(CANDIDATES, memory, budget=8, always_first="name")
    assert "empty-a" not in out["queries"]
    assert "empty-a" in out["retired"]


def test_a_first_run_behaves_exactly_as_before():
    """No history must mean no change. A loop that alters a first run is
    changing behaviour it has learned nothing about."""
    out = learning.plan(CANDIDATES, {}, budget=5, always_first="name")
    assert out["queries"] == CANDIDATES[:5]
    assert out["proven"] == []


def test_the_loop_says_what_it_did():
    out = learning.plan(CANDIDATES, {}, budget=5, always_first="name")
    assert "first run" in learning.explain(out)

    memory = {"worked-a": {"new_mentions": 9, "average": 9.0, "retired": False,
                           "asked": 2, "empty_in_a_row": 0},
              "empty-a": {"new_mentions": 0, "average": 0.0, "retired": True,
                          "asked": 2, "empty_in_a_row": 2}}
    said = learning.explain(learning.plan(CANDIDATES, memory, budget=5,
                                          always_first="name"))
    assert "remembered" in said
    assert "set aside" in said


# --- end to end ---------------------------------------------------------------

@pytest.fixture
def only_google_news(monkeypatch):
    # Discovered, not listed — see conftest.source_flags().
    from engine.tests.conftest import source_flags
    for flag in source_flags():
        monkeypatch.setattr(settings, flag, False, raising=False)
    monkeypatch.setattr(settings, "enable_google_news", True, raising=False)
    monkeypatch.setattr(settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(settings, "newsapi_key", "")


def test_a_second_run_asks_different_questions_from_the_first(
        db_session, only_google_news):
    politician = _subject(db_session)

    first = orchestrator.plan_run(db_session, politician, *WINDOW)
    first_queries = [t.query for t in db_session.query(IngestionTask)
                     .filter(IngestionTask.run_id == first.id).all()]
    # The first run finds material on one query and nothing on the rest.
    for task in (db_session.query(IngestionTask)
                 .filter(IngestionTask.run_id == first.id).all()):
        task.status = "done"
        task.result_count = 11 if task.query == first_queries[2] else 0
    first.status = "completed"
    first.started_at = datetime(2026, 5, 1)
    db_session.commit()

    second = orchestrator.plan_run(db_session, politician, *WINDOW)
    second_queries = [t.query for t in db_session.query(IngestionTask)
                      .filter(IngestionTask.run_id == second.id).all()]

    assert second_queries != first_queries, (
        "the second run asked exactly what the first one did")
    assert first_queries[2] in second_queries, (
        "the one query that found material was not repeated")
    assert second.stats.get("learning", {}).get("google_news"), (
        "the run does not record what its history changed")


def test_the_run_records_its_own_reasoning(db_session, only_google_news):
    politician = _subject(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)
    learned = run.stats["learning"]["google_news"]
    assert learned["remembered"] == 0
    assert "first run" in learned["note"]
    assert run.stats["prior_runs"] == 0


# --- the client can see it --------------------------------------------------

def test_the_report_carries_what_the_run_learned():
    """The corpus compounding is a thing the client is buying — the second
    report on a subject is worth more than the first — and it was happening
    entirely invisibly, which gives a client no reason to believe it is
    happening at all."""
    from engine.pipeline import _coverage_summary

    first = _coverage_summary({"prior_runs": 0, "learning": {
        "google_news": {"remembered": 0, "repeated": 0, "new_questions": 11,
                        "set_aside": 0, "note": "first run for this subject"}}})
    assert first["learning"]["remembered"] == 0
    assert "first run" in first["learning"]["note"]

    later = _coverage_summary({"prior_runs": 4, "learning": {
        "gdelt": {"remembered": 14, "repeated": 2, "new_questions": 2,
                  "set_aside": 3, "note": "14 queries remembered"},
        "google_news": {"remembered": 31, "repeated": 4, "new_questions": 5,
                        "set_aside": 9, "note": "31 queries remembered"}}})
    note = later["learning"]
    assert note["remembered"] == 45
    assert note["repeated"] == 6
    assert note["prior_runs"] == 4
    assert set(note["per_source"]) == {"gdelt", "google_news"}
    assert "4 previous run" in note["note"]


def test_a_run_with_no_history_does_not_dress_it_up_as_a_measurement():
    """"0 queries repeated" reads like a finding about this subject. It is
    the absence of one."""
    from engine.pipeline import _coverage_summary

    assert _coverage_summary({})["learning"] is None


def test_the_page_shows_it():
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    body = page[page.index("function renderDataCoverage("):]
    body = body[:body.index("\n  }\n")]
    assert "dc.learning" in body, (
        "the report does not show what the run learned, so the compounding "
        "corpus is invisible to the person paying for it")
    for shown in ("Repeated", "Asked first time", "Set aside"):
        assert shown in body, f"{shown} is not on the page"


# --- novelty is not quality --------------------------------------------------

def test_a_noisy_query_does_not_beat_a_useful_one():
    """`result_count` counts rows, so a query returning 120 rows nobody can
    connect to the subject would outscore one returning 6 that are about
    them — and the loop would learn that noise is the best strategy, while
    looking from the outside as though it were working.

    Ranking is on mentions entity linking tied to the subject, with novelty
    as the tie-break.
    """
    candidates = ["name", "noisy", "useful", "fresh-a", "fresh-b", "fresh-c"]
    memory = {
        "noisy": {"new_mentions": 120, "average": 120.0, "linked": 0,
                  "retired": False, "asked": 1, "empty_in_a_row": 0},
        "useful": {"new_mentions": 6, "average": 6.0, "linked": 5,
                   "retired": False, "asked": 1, "empty_in_a_row": 0},
    }
    out = learning.plan(candidates, memory, budget=4, always_first="name")
    assert out["ranked_by"] == "on-topic mentions"
    assert "useful" in out["proven"]
    assert "noisy" not in out["proven"], (
        "a query that returns nothing about the subject is being repeated "
        "because it returns a lot of it")


def test_the_weaker_signal_is_used_honestly_when_it_is_all_there_is():
    """Rows stored before mention-level task provenance cannot be attributed,
    so the loop falls back to novelty — and says which signal decided the
    order, rather than appearing to use the stronger one."""
    candidates = ["name", "noisy", "useful", "fresh-a"]
    memory = {
        "noisy": {"new_mentions": 120, "average": 120.0, "linked": None,
                  "retired": False, "asked": 1, "empty_in_a_row": 0},
        "useful": {"new_mentions": 6, "average": 6.0, "linked": None,
                   "retired": False, "asked": 1, "empty_in_a_row": 0},
    }
    out = learning.plan(candidates, memory, budget=3, always_first="name")
    assert out["ranked_by"] == "new mentions"
    assert "ranked by new mentions" in learning.explain(out)


def test_on_topic_counts_only_the_subject_s_own_entity(db_session):
    """A mention linked to "Ministry of Health" is not evidence that a query
    about Mudavadi worked. Counting any entity would teach the loop that the
    broadest, noisiest queries are the best — the exact failure the on-topic
    signal exists to prevent."""
    from engine.db.models import Entity, MentionEntity, RawMention

    politician = _subject(db_session)
    subject = Entity(name=politician.name, type="politician",
                     canonical_key=f"politician:{politician.name.lower()}")
    other = Entity(name="Ministry of Health", type="organization",
                   canonical_key="organization:ministry of health")
    db_session.add_all([subject, other])
    db_session.flush()

    run = IngestionRun(politician_id=politician.id, window_start=WINDOW[0],
                       window_end=WINDOW[1], status="completed",
                       started_at=datetime(2026, 5, 1))
    db_session.add(run)
    db_session.flush()
    on_q = IngestionTask(run_id=run.id, connector="google_news", platform="news",
                         endpoint="rss", query="on-topic", status="done",
                         result_count=2)
    off_q = IngestionTask(run_id=run.id, connector="google_news", platform="news",
                          endpoint="rss", query="off-topic", status="done",
                          result_count=2)
    db_session.add_all([on_q, off_q])
    db_session.flush()

    def _mention(task, text):
        row = RawMention(politician_id=politician.id, platform="news",
                         source_type="article", author_handle="nation.africa",
                         text=text, posted_at=datetime(2026, 6, 10),
                         content_hash=text, run_id=run.id, task_id=task.id,
                         is_spam=0)
        db_session.add(row)
        db_session.flush()
        return row

    linked_to_subject = _mention(on_q, "Mudavadi on foreign policy")
    linked_elsewhere = _mention(off_q, "Ministry of Health budget notes")
    db_session.add_all([
        MentionEntity(mention_id=linked_to_subject.id, entity_id=subject.id),
        MentionEntity(mention_id=linked_elsewhere.id, entity_id=other.id),
    ])
    db_session.commit()

    memory = learning.recall(db_session, politician, "google_news")
    assert memory["on-topic"]["linked"] == 1
    assert memory["off-topic"].get("linked") in (None, 0), (
        "a mention linked to a different entity counted as on-topic")


def test_a_mention_records_which_query_fetched_it():
    """The join the on-topic signal needs. Without it the loop can only ever
    learn novelty."""
    from engine.db.models import RawMention

    assert hasattr(RawMention, "task_id"), (
        "mentions do not record their task, so a relevance verdict cannot be "
        "attributed back to the query that fetched it")
    body = open("engine/ingestion/orchestrator.py", encoding="utf-8").read()
    assert '"task_id": task.id' in body, (
        "the column exists and nothing writes it")
