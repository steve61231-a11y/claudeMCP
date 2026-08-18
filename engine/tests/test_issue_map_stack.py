"""The issue map runs on the real acquisition stack.

It used to be a thin path around everything already built: four requests to
three sources with one literal AND-query each, no storage, and one of roughly
twenty analysis stages. That is why an issue map came back with four actors
while the politician report fanned out across dozens of identity variants and
eighty discovery probes.

These tests pin the three things that changed: the queries expand, the corpus
is stored under the principal so it compounds, and what earlier runs found is
read back on the next run.
"""

from datetime import datetime, timedelta

import pytest

from engine.db.models import Document, Politician, RawMention
from engine.ingestion import queries
from engine.reports import issue_map


# --------------------------------------------------------------------------
# Query expansion
# --------------------------------------------------------------------------

def _subject(**kwargs) -> Politician:
    return Politician(name=kwargs.pop("name", "John Mbadi"), **kwargs)


def test_intersection_crosses_every_identity_with_every_issue_name():
    """A Kenyan outlet writes "CS Mbadi" and "SHA", not the formal pair."""
    subject = _subject(aliases=["Mbadi"], titles=["CS"], subject_type="politician")
    variants = queries.intersection_text_variants(subject, "SHA", ["Social Health Authority"])

    assert '"John Mbadi" "SHA"' in variants
    assert '"CS Mbadi" "SHA"' in variants
    assert '"John Mbadi" "Social Health Authority"' in variants
    # The old behaviour was one query. Anything near that is a regression.
    assert len(variants) >= 6


def test_intersection_discovery_pulls_the_argument_apart():
    subject = _subject(aliases=["Mbadi"])
    probes = queries.intersection_discovery_variants(subject, "SHA")
    assert '"John Mbadi" "SHA"' in probes
    assert any(p.endswith(" court") for p in probes)
    assert any(p.endswith(" rollout") for p in probes)
    assert any(p.endswith(" history") for p in probes)  # recency ranking counterweight
    assert len(probes) >= 20


def test_pairs_are_breadth_first_over_identities():
    """A source with a small budget must still cover the primary name against
    every way the issue is written before spending on a second alias."""
    pairs = issue_map._pairs(["Primary", "Alias"], ["SHA", "Social Health Authority"], budget=3)
    assert pairs[0] == ("Primary", "SHA")
    assert ("Alias", "SHA") in pairs or ("Primary", "Social Health Authority") in pairs
    assert len(pairs) == 3


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------

def test_acquisition_sweeps_every_pair_not_one_query(monkeypatch):
    seen: list[tuple[str, str]] = []

    def _record(identity, term, ws, we):
        seen.append((identity, term))
        return []

    monkeypatch.setattr(issue_map.settings, "enable_gdelt", True, raising=False)
    monkeypatch.setattr(issue_map.settings, "enable_google_news", False, raising=False)
    monkeypatch.setattr(issue_map.settings, "enable_reddit", False, raising=False)
    monkeypatch.setattr(issue_map.settings, "enable_youtube", False, raising=False)
    monkeypatch.setattr(issue_map, "_gdelt_intersection", _record)

    issue_map.acquire_intersection(
        "John Mbadi", "SHA", datetime(2026, 1, 1), datetime(2026, 6, 1),
        identities=["John Mbadi", "CS Mbadi", "Mbadi"],
        issue_terms=["SHA", "Social Health Authority"],
    )
    assert len(seen) == min(6, issue_map.PAIR_BUDGET["gdelt"])
    assert ("John Mbadi", "SHA") in seen


def test_one_failing_query_does_not_end_the_sweep(monkeypatch):
    calls: list[tuple[str, str]] = []

    def _flaky(identity, term, ws, we):
        calls.append((identity, term))
        if len(calls) == 1:
            raise RuntimeError("upstream 503")
        return []

    monkeypatch.setattr(issue_map.settings, "enable_gdelt", True, raising=False)
    monkeypatch.setattr(issue_map.settings, "enable_google_news", False, raising=False)
    monkeypatch.setattr(issue_map.settings, "enable_reddit", False, raising=False)
    monkeypatch.setattr(issue_map.settings, "enable_youtube", False, raising=False)
    monkeypatch.setattr(issue_map, "_gdelt_intersection", _flaky)

    issue_map.acquire_intersection(
        "John Mbadi", "SHA", datetime(2026, 1, 1), datetime(2026, 6, 1),
        identities=["A", "B", "C"], issue_terms=["SHA"],
    )
    assert len(calls) == 3


def test_google_news_gets_a_both_terms_query():
    """The connector quotes the name it is given, so the shim has to produce
    two quoted phrases rather than one."""
    assert issue_map._both_terms_for_quoting_source("Ruto", "SHA") == 'Ruto" "SHA'


# --------------------------------------------------------------------------
# Persistence and reuse — the point of the whole exercise
# --------------------------------------------------------------------------

def test_the_intersection_corpus_is_stored_and_read_back(db_session):
    from engine.agents import evidence

    subject = Politician(name="Reuse Probe")
    db_session.add(subject)
    db_session.flush()

    db_session.add(
        Document(
            politician_id=subject.id, url="http://news.example/sha-rollout",
            domain="news.example", title="SHA rollout stalls in counties",
            body="Facilities report that patients registered under SHA are still asked for cash. " * 20,
            source="searxng", content_hash="sha-doc-1",
            published_at=datetime(2026, 6, 2),
        )
    )
    db_session.add(
        RawMention(
            politician_id=subject.id, platform="reddit", source_type="post",
            author_handle="r/Kenya", text="The SHA transition has been rough for chronic patients.",
            posted_at=datetime(2026, 6, 3), content_hash="sha-mention-1", is_spam=0,
        )
    )
    db_session.add(
        Document(
            politician_id=subject.id, url="http://sports.example/x", domain="sports.example",
            title="Basketball recap", body="Scores and standings. " * 100,
            source="searxng", content_hash="noise-doc-1",
        )
    )
    db_session.commit()

    corpus = evidence.retrieve_intersection(db_session, subject.id, ["SHA"])
    texts = " ".join(c["text"] for c in corpus)
    assert "SHA rollout stalls" in texts
    assert "chronic patients" in texts
    assert "Basketball" not in texts
    # Shaped like the rest of the corpus so the digest needs no special-casing.
    assert {"id", "platform", "source_type", "text", "posted_at"} <= set(corpus[0])


def test_off_topic_documents_stay_out_of_the_intersection(db_session):
    """The disambiguation gate's verdict has to be honoured on retrieval too,
    or the SHA-is-also-a-hash problem walks straight back in."""
    from engine.agents import evidence

    subject = Politician(name="Gate Probe")
    db_session.add(subject)
    db_session.flush()
    db_session.add(
        Document(
            politician_id=subject.id, url="http://crypto.example/sha256",
            domain="crypto.example", title="SHA-256 explained",
            body="SHA is a family of cryptographic hash functions. " * 40,
            source="searxng", content_hash="sha256-doc", relevance_verdict="off_topic",
        )
    )
    db_session.commit()
    assert evidence.retrieve_intersection(db_session, subject.id, ["SHA"]) == []


def test_a_second_run_starts_from_what_the_first_one_found(db_session, monkeypatch):
    """The whole reason to store: run two must not begin from zero."""
    from engine.agents import evidence

    subject = Politician(name="Compounding Probe")
    db_session.add(subject)
    db_session.flush()
    db_session.commit()

    assert evidence.retrieve_intersection(db_session, subject.id, ["SHA"]) == []

    stored = issue_map._persist_intersection(
        db_session, subject,
        [
            {
                "platform": "news.example", "source_type": "article",
                "author_handle": "news.example",
                "text": "The SHA levy was defended in parliament this week by the minister.",
                "posted_at": datetime(2026, 6, 4), "engagement": {},
                "raw_payload": {"url": "http://news.example/sha-levy"},
            }
        ],
        [
            {
                "url": "http://county.example/sha-report", "domain": "county.example",
                "title": "County SHA implementation report",
                "body": "Registration under SHA reached sixty percent of households. " * 30,
                "source": "searxng",
            }
        ],
        issue="SHA",
    )
    assert stored["mentions_stored"] == 1
    assert stored["documents_stored"] == 1

    found = evidence.retrieve_intersection(db_session, subject.id, ["SHA"])
    assert len(found) == 2


def test_storing_the_same_material_twice_does_not_duplicate_it(db_session):
    from engine.agents import evidence

    subject = Politician(name="Idempotence Probe")
    db_session.add(subject)
    db_session.flush()
    db_session.commit()

    payload = (
        [
            {
                "platform": "news.example", "source_type": "article",
                "author_handle": "news.example",
                "text": "The SHA levy was defended in parliament this week by the minister.",
                "posted_at": datetime(2026, 6, 4), "engagement": {},
                "raw_payload": {"url": "http://news.example/sha-levy"},
            }
        ],
        [
            {
                "url": "http://county.example/sha-report", "domain": "county.example",
                "title": "County SHA implementation report",
                "body": "Registration under SHA reached sixty percent of households. " * 30,
                "source": "searxng",
            }
        ],
    )
    issue_map._persist_intersection(db_session, subject, *payload, issue="SHA")
    second = issue_map._persist_intersection(db_session, subject, *payload, issue="SHA")

    assert second["mentions_stored"] == 0
    assert second["documents_stored"] == 0
    assert len(evidence.retrieve_intersection(db_session, subject.id, ["SHA"])) == 2


def test_the_corpus_union_never_reads_the_same_article_twice():
    stored = [{"source_url": "http://a/1", "text": "one"}]
    fresh = [{"source_url": "http://a/1", "text": "one"}, {"source_url": "http://a/2", "text": "two"}]
    merged = issue_map._merge_corpus(stored, fresh)
    assert len(merged) == 2


def test_injected_mentions_still_skip_acquisition_entirely(monkeypatch):
    """Tests and callers holding a corpus must never trigger a live sweep."""
    def _boom(*a, **k):
        raise AssertionError("acquisition ran despite injected mentions")

    monkeypatch.setattr(issue_map, "_acquire_and_store", _boom)
    monkeypatch.setattr("engine.reports.digest.build_corpus_digest",
                        lambda label, mentions: {"coverage": {"mentions_total": len(mentions)}})
    monkeypatch.setattr("engine.reports.analysts.analyze_issue_intersection",
                        lambda *a, **k: {"key_actors": [], "timeline": [], "involvement": ""})

    payload = issue_map.build_issue_map("A", "B", mentions=[{"text": "x"}])
    assert payload["coverage"]["mentions_total"] == 1


def test_stages_are_published_as_the_map_is_built(monkeypatch):
    published: list[str] = []
    monkeypatch.setattr(issue_map, "_acquire_and_store",
                        lambda *a, **k: ([{"text": "x", "platform": "news"}], {"stored": True}))
    monkeypatch.setattr("engine.reports.digest.build_corpus_digest",
                        lambda label, mentions: {"coverage": {"mentions_total": len(mentions)}})
    monkeypatch.setattr("engine.reports.analysts.analyze_issue_intersection",
                        lambda *a, **k: {"key_actors": [], "timeline": [], "involvement": "x"})

    issue_map.build_issue_map("A", "B", on_section=lambda k, v: published.append(k))
    assert "coverage" in published
    assert "intersection" in published
    assert published.index("coverage") < published.index("intersection")


def test_a_broken_reader_never_costs_the_map(monkeypatch):
    monkeypatch.setattr(issue_map, "_acquire_and_store",
                        lambda *a, **k: ([{"text": "x"}], {}))
    monkeypatch.setattr("engine.reports.digest.build_corpus_digest",
                        lambda label, mentions: {"coverage": {"mentions_total": 1}})
    monkeypatch.setattr("engine.reports.analysts.analyze_issue_intersection",
                        lambda *a, **k: {"verdict": "v"})

    def boom(key, value):
        raise RuntimeError("reader went away")

    payload = issue_map.build_issue_map("A", "B", on_section=boom)
    assert payload["intersection"]["verdict"] == "v"
