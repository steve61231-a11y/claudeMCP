"""A whole analysis run, against a real database, with a scripted model.

Every defect found in this pipeline so far has been an INTEGRATION defect: a
key written under one name and read under another, a count that included
failures, a filter that deleted what it was meant to validate. Unit tests could
not see any of them, because each component was correct on its own.

This runs `run_analysis` end to end and asserts the properties a report must
have whatever the corpus is. The model is scripted so the run is deterministic
and free; what is under test is the wiring, not the analysis.
"""

from datetime import datetime, timedelta

import pytest

from engine import health, llm, stages
from engine.db.models import Politician, RawMention
from engine.pipeline import run_analysis

WINDOW_DAYS = 30


def _scripted(prompt, max_tokens=None, model=None):
    """A model that answers every prompt in this pipeline plausibly."""
    p = prompt.lower()
    if "reply with only this json" in p:                      # preflight
        return {"ok": True, "n": 2}
    if "narrative theme" in p or '"clusters"' in p:
        return {"clusters": [{"id": i, "label": f"Storyline {i}",
                              "description": "What this cluster is about."} for i in range(6)]}
    if '"scores"' in p or "sentiment" in p and '"i"' in p:
        return {"scores": [{"i": i, "sentiment": "negative", "intensity": 4,
                            "stance": "critical", "topic": "cost of living",
                            "language": "en"} for i in range(1, 40)]}
    if '"people"' in p:
        return {"people": [{"i": 1, "name": "Jane Mwangi", "role": "senator"}]}
    if '"verdicts"' in p:
        return {"verdicts": [{"i": i, "verdict": "on_topic", "confidence": 0.9,
                              "reason": "names the subject"} for i in range(1, 40)]}
    if '"claims"' in p:
        return {"claims": [{"i": 1, "text": "A checkable assertion."}]}
    if '"digest"' in p:
        return {"digest": {"claims": [{"ref": "aaaa", "text": "A claim."}],
                           "themes": [{"theme": "cost of living", "count": 5}],
                           "notable_quotes": [], "entities": [],
                           "sentiment_read": {"supportive": 1, "critical": 4, "neutral": 2},
                           "anomalies": []}}
    if '"public_voice"' in p:
        return {"public_voice": {"supportive": [], "critical": [], "neutral": []}}
    if '"summary"' in p:
        return {"summary": "A written executive summary of the period."}
    if '"insights"' in p:
        return {"insights": [{"headline": "H", "reasoning": "R", "confidence": "medium"}],
                "the_one_thing": "The single most important thing."}
    return {}


@pytest.fixture()
def scripted_model(monkeypatch):
    monkeypatch.setattr(llm, "_call_json", _scripted)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "test/bulk")
    monkeypatch.setattr(llm, "strong_model", lambda: "test/strong")
    monkeypatch.setattr(llm, "concurrency", lambda n: 1)
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    health.reset()
    stages.reset()
    yield
    health.reset()
    stages.reset()


def _seed(db_session, count=40, name="Edwin Sifuna"):
    subject = Politician(name=name, aliases=["Sifuna"], titles=["Senator"],
                         swahili_terms=[], subject_type="politician")
    db_session.add(subject)
    db_session.flush()
    now = datetime.utcnow()
    for i in range(count):
        db_session.add(RawMention(
            politician_id=subject.id,
            platform="nation.africa" if i % 2 else "youtube",
            source_type="article" if i % 2 else "post",
            author_handle=f"author{i}",
            text=f"{name} spoke about the cost of living in Nairobi, item number {i}. "
                 f"This is a distinct story with its own details number {i}.",
            posted_at=now - timedelta(days=i % WINDOW_DAYS),
            engagement_json={"views": 100 + i},
            source_url=f"https://example.co.ke/story-{i}",
            is_spam=0,
        ))
    db_session.commit()
    return subject


def _run(db_session, subject):
    now = datetime.utcnow()
    return run_analysis(db_session, subject, "weekly",
                        now - timedelta(days=WINDOW_DAYS), now)


# --- the run completes and reports honestly ---------------------------------

def test_a_healthy_run_produces_a_report(db_session, scripted_model):
    subject = _seed(db_session)
    report = _run(db_session, subject)
    assert report.payload, "a completed run must carry a payload"


def test_a_healthy_run_is_marked_usable(db_session, scripted_model):
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    assert payload["run_health"]["usable"] is True
    assert payload["run_health"]["headline"] is None, "a healthy run must not warn"


def test_coverage_never_claims_more_than_it_read(db_session, scripted_model):
    """The defect that printed "188 · every item read" while every call 404'd."""
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    coverage = payload.get("coverage") or {}
    if coverage:
        assert coverage["mentions_analyzed"] <= coverage["mentions_total"]
        if coverage.get("chunks_failed"):
            assert coverage["complete"] is False


def test_narratives_are_never_numbered_placeholders(db_session, scripted_model):
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    for narrative in payload.get("narrative_breakdown") or []:
        assert not narrative["label"].lower().startswith("narrative-"), narrative["label"]


def test_the_section_ledger_travels_with_the_report(db_session, scripted_model):
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    assert "section_status" in payload
    assert isinstance(payload["section_status"]["stages"], list)


def test_the_payload_is_json_serialisable(db_session, scripted_model):
    """It is stored as JSON and sent over the API; a stray datetime or set here
    fails at the boundary, long after the expensive work is done."""
    import json

    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    json.dumps(payload, default=str)


# --- a dead model must stop the run, not produce an empty report ------------

def test_a_dead_model_stops_the_run_before_any_work(db_session, monkeypatch):
    def dead(*a, **k):
        raise RuntimeError("HTTP 404 (model='stealth/ox-alpha') Thank you for participating")

    monkeypatch.setattr(llm, "_call_json", dead)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "stealth/ox-alpha")
    health.reset()
    stages.reset()

    subject = _seed(db_session)
    with pytest.raises(health.PreflightFailed) as caught:
        _run(db_session, subject)
    assert "LLM_MODEL" in caught.value.remedy


def test_the_preflight_failure_names_the_model(db_session, monkeypatch):
    monkeypatch.setattr(llm, "_call_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 404")))
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "stealth/ox-alpha")
    health.reset()
    subject = _seed(db_session, name="Uhuru Kenyatta")
    with pytest.raises(health.PreflightFailed) as caught:
        _run(db_session, subject)
    assert "stealth/ox-alpha" in caught.value.remedy


# --- a partially dead model degrades visibly --------------------------------

def test_sections_that_fail_are_named_not_silently_empty(db_session, monkeypatch):
    """Preflight passes, then the provider dies. The report must say which
    sections could not be produced rather than presenting them as empty."""
    state = {"calls": 0}

    def flaky(prompt, max_tokens=None, model=None):
        state["calls"] += 1
        if state["calls"] > 3:
            raise RuntimeError("HTTP 429 rate limit exceeded")
        return _scripted(prompt, max_tokens, model)

    monkeypatch.setattr(llm, "_call_json", flaky)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "test/bulk")
    monkeypatch.setattr(llm, "strong_model", lambda: "test/strong")
    monkeypatch.setattr(llm, "concurrency", lambda n: 1)
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    health.reset()
    stages.reset()

    subject = _seed(db_session)
    payload = _run(db_session, subject).payload

    assert payload["run_health"]["failures"] > 0
    assert payload["run_health"]["headline"], "a degraded run must say so"
    status = payload["section_status"]
    assert status["failed_count"] > 0, "failed sections must be named, not blank"


# --- the API boundary, where name-mismatch bugs live ------------------------

def _api_shape(payload, db_session=None, subject_id=None):
    """Render the payload the way the API does, so a key written under one name
    and read under another shows up here rather than as a blank tab."""
    from unittest.mock import patch

    from engine import api_server
    from engine.api_server import _build_frontend_payload

    class _Report:
        def __init__(self, payload):
            self.payload = payload
            self.window_start = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
            self.window_end = datetime.utcnow()
            self.generated_at = datetime.utcnow()
            self.id = "r1"

    class _Subject:
        name = "Edwin Sifuna"
        id = subject_id
        subject_type = "politician"
        titles = ["Senator"]

    # The builder opens its own session; point it at the test database rather
    # than the deployment default.
    with patch.object(api_server, "SessionLocal", lambda: db_session):
        return _build_frontend_payload(_Subject(), _Report(payload))


def test_the_api_exposes_every_section_the_frontend_reads(db_session, scripted_model):
    """Section 6.0 said "No dominant issue identified" on every report ever
    produced because the framework read `narratives` while the payload wrote
    `narrative_breakdown`. These are the keys the page actually reads."""
    subject = _seed(db_session)
    response = _api_shape(_run(db_session, subject).payload, db_session, subject.id)
    for key in ("narratives", "sentiment", "volume", "coverage", "runHealth",
                "sectionStatus", "grade"):
        assert key in response, f"the frontend reads {key!r} and the API does not send it"


def test_narratives_reach_the_api_with_their_evidence(db_session, scripted_model):
    subject = _seed(db_session)
    response = _api_shape(_run(db_session, subject).payload, db_session, subject.id)
    for narrative in response["narratives"]:
        assert "evidence" in narrative, "a narrative the reader cannot open is an assertion"
        assert not narrative["label"].lower().startswith("narrative-")


def test_run_health_and_section_status_reach_the_api(db_session, scripted_model):
    subject = _seed(db_session)
    response = _api_shape(_run(db_session, subject).payload, db_session, subject.id)
    assert response["runHealth"]["usable"] is True
    assert isinstance(response["sectionStatus"]["stages"], list)


def test_the_api_response_is_json_serialisable(db_session, scripted_model):
    import json

    subject = _seed(db_session)
    json.dumps(_api_shape(_run(db_session, subject).payload, db_session, subject.id), default=str)


def test_a_degraded_run_carries_its_warning_to_the_api(db_session, monkeypatch):
    state = {"calls": 0}

    def flaky(prompt, max_tokens=None, model=None):
        state["calls"] += 1
        if state["calls"] > 3:
            raise RuntimeError("HTTP 429 rate limit exceeded")
        return _scripted(prompt, max_tokens, model)

    monkeypatch.setattr(llm, "_call_json", flaky)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "test/bulk")
    monkeypatch.setattr(llm, "strong_model", lambda: "test/strong")
    monkeypatch.setattr(llm, "concurrency", lambda n: 1)
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    health.reset()
    stages.reset()

    subject = _seed(db_session)
    response = _api_shape(_run(db_session, subject).payload, db_session, subject.id)
    assert response["runHealth"]["headline"], "the page must be told the run degraded"
    assert response["sectionStatus"]["failed_count"] > 0


# --- the client deliverable -------------------------------------------------

def _framework(payload):
    return payload.get("sentiment_framework") or {}


def test_the_framework_is_produced_on_a_healthy_run(db_session, scripted_model):
    """The Sentiment Framework tab simply never appeared when build() raised,
    which is indistinguishable from a subject it had nothing to say about."""
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    framework = _framework(payload)
    assert framework, "the client deliverable did not appear at all"
    for section in ("summary_of_subject", "sentiment_score", "overall_mentions",
                    "sentiment", "current_issues", "emergent_issues",
                    "strategic_implications"):
        assert section in framework, f"framework section {section!r} is missing"


def test_the_framework_reads_the_same_corpus_the_overview_does(db_session, scripted_model):
    """"Mentions 661" beside "Total mentions 852" with no explanation. The two
    numbers are differently scoped and both true; what is not acceptable is
    the framework counting mentions the overview never saw."""
    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    overview = (payload.get("volume_trends") or {}).get("total_mentions")
    framework_total = _framework(payload).get("overall_mentions", {}).get("total")
    assert framework_total is not None and overview is not None
    assert framework_total >= overview, (
        "the framework corpus includes documents as well as mentions, so it may be "
        "larger — but never smaller than what the overview counted")


def test_the_sentiment_score_explains_itself(db_session, scripted_model):
    subject = _seed(db_session)
    score = _framework(_run(db_session, subject).payload).get("sentiment_score") or {}
    assert score.get("reading"), "a headline percentage with no reading is unusable"
    assert len(score["reading"]) >= 3


def test_sources_carry_their_own_evidence(db_session, scripted_model):
    """"Sources covered (73)" was a headcount nobody could open."""
    subject = _seed(db_session)
    volume = _framework(_run(db_session, subject).payload).get("overall_mentions") or {}
    detail = volume.get("sources_detail") or []
    assert detail, "sources reached the page as a bare count"
    assert any(source.get("top_mentions") for source in detail)


def test_a_lean_is_never_asserted_on_too_little_scoring(db_session, scripted_model):
    subject = _seed(db_session)
    volume = _framework(_run(db_session, subject).payload).get("overall_mentions") or {}
    for source in volume.get("sources_detail") or []:
        if source["scored"] < 3:
            assert source["lean"] is None, (
                f"{source['source']} was called {source['lean']!r} off "
                f"{source['scored']} scored item(s)")


def test_key_people_are_not_broadcast_channels(db_session, scripted_model):
    """"Key people" listed cpnnewz, plugtvkenya, simbatv_ke — YouTube channels."""
    subject = _seed(db_session)
    implications = _framework(_run(db_session, subject).payload).get(
        "strategic_implications") or {}
    handles = {a.get("handle") for a in implications.get("key_amplifiers") or []}
    names = {p.get("name") for p in implications.get("key_people") or []}
    assert not (handles & names), "an amplifier handle is being presented as a person"


def test_percentages_are_absent_rather_than_zero_when_nothing_scored(db_session, monkeypatch):
    """A ring reading 0.0% asserts the subject has no positive coverage. When
    nothing was scored that is a failure wearing a finding's clothes."""
    def no_scoring(prompt, max_tokens=None, model=None):
        if '"scores"' in prompt.lower():
            raise RuntimeError("HTTP 429 rate limit exceeded")
        return _scripted(prompt, max_tokens, model)

    monkeypatch.setattr(llm, "_call_json", no_scoring)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "test/bulk")
    monkeypatch.setattr(llm, "strong_model", lambda: "test/strong")
    monkeypatch.setattr(llm, "concurrency", lambda n: 1)
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    health.reset()
    stages.reset()

    subject = _seed(db_session)
    payload = _run(db_session, subject).payload
    breakdown = payload.get("sentiment_breakdown") or {}
    if not breakdown.get("total_mentions_analyzed"):
        assert breakdown.get("positive_pct") is None, "0.0% off nothing scored is a lie"
        score = _framework(payload).get("sentiment_score") or {}
        assert score.get("score") is None
        assert score.get("scoring_gap"), "say that nothing was scored"
