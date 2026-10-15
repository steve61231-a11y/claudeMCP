"""Twelve queries were planned and the same one was sent twelve times.

`queries.connector_variants` expands a subject into the queries a source
should actually run — the name, the investigator's leads from last run, the
aliases and titles, and a rotating slice of the probe list. `plan_run`
creates one task per variant: twelve for Google News, six for GDELT, four
for Reddit, three for YouTube.

Then `_execute_bulk_connector_task` called

    connector.fetch(politician.name, politician.aliases, start, end)

and no connector took a query at all. Every one re-derived its own search
phrase from the name and the aliases. So twelve tasks each carrying a
distinct, carefully rationed query ran the identical bare-name request,
twelve times, and the dedupe threw eleven of them away. The requests were
paid for and the coverage was not.

Which makes the fix nearly free: the volume was already being spent. The
same twelve requests now ask twelve different questions.

This is the failure mode this repository keeps paying for, and the file
that pins the expansion is the evidence: every test in
`test_the_search_is_more_than_a_name.py` calls `connector_variants`
directly. Not one asserted that a variant reached a source. A helper can be
perfect and unused, and a test that calls the helper cannot tell.
"""

from datetime import datetime

import pytest

from engine.config import settings
from engine.db.models import IngestionTask, Politician
from engine.ingestion import orchestrator, queries

WINDOW = (datetime(2026, 6, 1), datetime(2026, 6, 30, 23, 59, 59))

#: Sources whose search phrase is a keyword query, and which therefore have
#: to be told which one to run. Facebook (a page list), Wayback (a CDX
#: filter over fixed domains) and Wikipedia (a title lookup) are not keyword
#: searches and are deliberately absent.
KEYWORD_SOURCES = ("gdelt", "google_news", "reddit", "youtube",
                   "agentreach", "kenya_gazette", "kenya_law")


def _subject(db):
    politician = Politician(
        name="Musalia Mudavadi",
        aliases=["Mudavadi"],
        keywords=["Foreign Affairs"],
        titles=["Prime Cabinet Secretary"],
        swahili_terms=[],
        tracked_hashtags=["Mudavadi"],
        social_handles={},
    )
    db.add(politician)
    db.commit()
    return politician


@pytest.fixture
def only(monkeypatch):
    """Enable exactly one source, so the queries observed are its own."""
    def _enable(name):
        # Every source flag, found rather than listed.
        #
        # This was a hand-maintained list, so adding the Kenya Gazette and
        # Kenya Law connectors silently broke it: their tasks still ran, the
        # recorder caught their queries, and the assertion blamed gdelt for
        # running four queries it had never been given. A test whose setup
        # has to be updated for every new connector is a test that reports
        # the next connector as a bug in the last one.
        #
        # `enable_verification` and friends are not sources, so only flags
        # the orchestrator actually plans tasks from are touched.
        source_flags = {
            f for f in dir(settings)
            if f.startswith("enable_") and f not in (
                "enable_verification", "enable_resolution",
                "enable_investigator", "enable_article_text",
                "enable_scrapling", "enable_scrapling_stealth")
        }
        for flag in source_flags:
            monkeypatch.setattr(settings, flag, False, raising=False)
        monkeypatch.setattr(settings, "socialcrawl_api_key", "")
        monkeypatch.setattr(settings, "newsapi_key", "")
        monkeypatch.setattr(settings, f"enable_{name}", True, raising=False)
    return _enable


class _Recorder:
    """Stands in for a connector and writes down what it was asked."""

    def __init__(self):
        self.asked: list[str | None] = []
        self.last_error = None

    def fetch(self, politician_name, aliases, window_start, window_end, query=None):
        self.asked.append(query)
        return []


@pytest.mark.parametrize("source", KEYWORD_SOURCES)
def test_each_planned_variant_is_the_query_that_actually_runs(
        db_session, monkeypatch, only, source):
    only(source)
    politician = _subject(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)

    planned = [t.query for t in db_session.query(IngestionTask)
               .filter(IngestionTask.run_id == run.id,
                       IngestionTask.connector == source).all()]
    assert len(planned) > 1, (
        f"{source} planned {len(planned)} task(s) — the expansion is not "
        "reaching plan_run at all")

    recorder = _Recorder()
    monkeypatch.setattr(orchestrator, "_build_connector",
                        lambda task: recorder)
    monkeypatch.setattr(orchestrator, "_store_mentions",
                        lambda *a, **k: 0)
    orchestrator.execute_run(db_session, run.id)

    assert sorted(q for q in recorder.asked if q) == sorted(planned), (
        f"{source} planned {len(set(planned))} distinct queries and ran "
        f"{len(set(recorder.asked))}: {recorder.asked[:4]}")


@pytest.mark.parametrize("source", KEYWORD_SOURCES)
def test_the_probes_are_among_the_queries_that_run(db_session, monkeypatch, only, source):
    """The bare name is slot one and always survives, so a test that only
    counted distinct queries could pass on twelve spellings of the name.
    What the expansion exists for is the probe list — the court record, the
    contracts, the earlier career — and that is what has to arrive."""
    only(source)
    politician = _subject(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)

    recorder = _Recorder()
    monkeypatch.setattr(orchestrator, "_build_connector", lambda task: recorder)
    monkeypatch.setattr(orchestrator, "_store_mentions", lambda *a, **k: 0)
    orchestrator.execute_run(db_session, run.id)

    # The probes THIS source is asked from. Kenya Law holds no journalism,
    # so "scandal" and "reaction" appear in none of its documents; asserting
    # the news list against it would demand the planner ask a question that
    # can only come back empty.
    probes = set(queries.probes_for(source))
    ran = " | ".join(q for q in recorder.asked if q).lower()
    assert any(probe in ran for probe in probes), (
        f"{source} ran no probe query at all: {recorder.asked[:4]}")


def test_a_connector_that_is_not_a_keyword_search_is_left_alone(
        db_session, monkeypatch, only):
    """Wayback filters a CDX index over a fixed domain list by surname, and
    Wikipedia looks up a title. Handing either a probe query would turn a
    working source into an empty one — the fix has to know which sources it
    is for."""
    only("wayback")
    politician = _subject(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)
    planned = [t.query for t in db_session.query(IngestionTask)
               .filter(IngestionTask.run_id == run.id,
                       IngestionTask.connector == "wayback").all()]
    assert planned == [politician.name], (
        "wayback is being handed query variants it cannot use")


def test_every_keyword_connector_accepts_the_query_it_is_handed():
    """The set and the signatures have to agree.

    A connector in KEYWORD_CONNECTORS whose `fetch` has no `query`
    parameter raises TypeError the first time a real run reaches it — and
    the guard around connector failures would swallow it as "this source
    returned nothing", which is the one thing this product does not allow a
    failure to look like.
    """
    import inspect

    from engine.db.models import IngestionTask as _Task

    for name in sorted(orchestrator.KEYWORD_CONNECTORS):
        connector = orchestrator._build_connector(_Task(connector=name))
        signature = inspect.signature(connector.fetch)
        assert "query" in signature.parameters, (
            f"{name} is in KEYWORD_CONNECTORS but its fetch() takes no "
            "query, so a planned variant would raise TypeError at runtime")
        assert signature.parameters["query"].default is None, (
            f"{name}.fetch's query is not optional, which breaks every "
            "caller that does not plan tasks")


def test_x_is_asked_more_than_its_own_three_terms(db_session, monkeypatch, only):
    """Both X backends expanded internally to the name plus two aliases,
    from their own list, so the place the political conversation actually
    happens was never asked about the court record or the contracts.

    The budget stays small deliberately: these drive a logged-in burner
    account and X rate-limits scrapers hard, so the cost of being greedy is
    a locked account rather than a slow run.
    """
    for source in ("twscrape", "scweet"):
        only(source)
        monkeypatch.setattr(settings, "x_username", "burner", raising=False)
        monkeypatch.setattr(settings, "x_password", "secret", raising=False)
        politician = _subject(db_session)
        run = orchestrator.plan_run(db_session, politician, *WINDOW)
        planned = [t.query for t in db_session.query(IngestionTask)
                   .filter(IngestionTask.run_id == run.id,
                           IngestionTask.connector == source).all()]
        assert len(planned) > 1, f"{source} still gets one bare-name task"
        assert len(planned) <= 6, (
            f"{source} plans {len(planned)} X searches — that is an account "
            "ban, not more data")
        probes = set(queries.DISCOVERY_PROBES) | set(queries.DISCOVERY_TIMESPAN_PROBES)
        assert any(p in " ".join(planned).lower() for p in probes), (
            f"{source} is asked more queries but still no probe")
