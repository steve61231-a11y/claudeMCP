"""Source credibility — "three sources say so" is meaningless until you know which three.

These tests pin the judgements that keep corroboration honest: a source's
character sets a prior, observed behaviour moves it, an unknown source with one
story earns little, and claim confidence reflects the QUALITY of what backs it
rather than only the count.
"""

from datetime import datetime

from engine.agents.credibility import (
    TYPE_PRIORS,
    classify_source,
    compute_score,
    credibility_for,
    score_sources,
    weighted_confidence,
)
from engine.db.models import Document, Event, EventEvidence, Politician, SourceCredibility


def _subject(db_session, name="Cred Probe"):
    p = Politician(name=name, aliases=[], titles=[], keywords=[], swahili_terms=[])
    db_session.add(p)
    db_session.flush()
    return p


def _doc(db_session, subject, domain, h):
    d = Document(politician_id=subject.id, title="t", body="b", content_hash=h,
                 domain=domain, url=f"https://{domain}/{h}", source="searxng",
                 fetched_at=datetime.utcnow())
    db_session.add(d)
    db_session.flush()
    return d


# --- classification --------------------------------------------------------

def test_structural_classification_of_sources():
    assert classify_source("treasury.go.ke") == "official"
    assert classify_source("reuters.com") == "wire"
    assert classify_source("en.wikipedia.org") == "encyclopedia"
    assert classify_source("someone.blogspot.com") == "blog"
    assert classify_source("@handle") == "social"
    assert classify_source("randomnews.co.ke") == "digital"


def test_social_platform_overrides_handle_shape():
    assert classify_source("someuser", platform="twitter") == "social"


# --- scoring ---------------------------------------------------------------

def test_official_records_outrank_anonymous_blogs():
    official, _ = compute_score("official", corroboration_rate=0.5, observations=3)
    blog, _ = compute_score("blog", corroboration_rate=0.5, observations=3)
    assert official > blog


def test_one_story_from_an_unknown_source_earns_little_weight():
    """A brand-new domain with a single story is not a track record."""
    _, thin = compute_score("digital", corroboration_rate=1.0, observations=1)
    _, established = compute_score("digital", corroboration_rate=1.0, observations=200)
    assert thin["history_weight"] == 0.0
    assert established["history_weight"] == 1.0


def test_observed_corroboration_moves_the_score_once_there_is_history():
    borne_out, _ = compute_score("digital", corroboration_rate=1.0, observations=200, independence=1.0)
    never_matched, _ = compute_score("digital", corroboration_rate=0.0, observations=200, independence=0.3)
    assert borne_out > never_matched


def test_score_breakdown_is_always_explainable():
    _, components = compute_score("mainstream", 0.6, 30)
    for field in ("prior", "type", "corroboration_rate", "independence", "observations", "history_weight"):
        assert field in components, f"missing '{field}' — a score must be explainable"


# --- confidence weighting --------------------------------------------------

def test_strong_sources_beat_a_larger_number_of_weak_ones():
    """The point of credibility: quality must be able to outweigh quantity."""
    scores = {"gov.example": 0.9, "reuters.example": 0.85,
              "blog1.example": 0.2, "blog2.example": 0.2, "blog3.example": 0.2}

    two_strong = weighted_confidence(0.75, ["gov.example", "reuters.example"], scores)
    three_weak = weighted_confidence(0.9, ["blog1.example", "blog2.example", "blog3.example"], scores)

    assert two_strong > three_weak


def test_claim_with_no_sources_is_discounted():
    assert weighted_confidence(0.8, [], {}) < 0.8


# --- persistence -----------------------------------------------------------

def test_scores_are_computed_and_stored_for_corpus_sources(db_session):
    subject = _subject(db_session)
    _doc(db_session, subject, "treasury.go.ke", "c1")
    _doc(db_session, subject, "someone.blogspot.com", "c2")
    db_session.commit()

    result = score_sources(db_session, subject)

    assert result["scored"] == 2
    stored = {r.key: r for r in db_session.query(SourceCredibility).all()}
    assert stored["treasury.go.ke"].source_type == "official"
    assert stored["treasury.go.ke"].score > stored["someone.blogspot.com"].score
    assert stored["treasury.go.ke"].components["prior"] == TYPE_PRIORS["official"]


def test_corroborated_sources_score_above_never_corroborated_ones(db_session):
    """A source whose stories others also carry is being borne out."""
    subject = _subject(db_session)
    a = _doc(db_session, subject, "corroborated-a.example", "k1")
    b = _doc(db_session, subject, "corroborated-b.example", "k2")
    lone = _doc(db_session, subject, "lonely.example", "k3")
    shared = Event(politician_id=subject.id, title="Shared story", dedupe_key="d1")
    solo = Event(politician_id=subject.id, title="Exclusive story", dedupe_key="d2")
    db_session.add_all([shared, solo])
    db_session.flush()
    db_session.add_all([
        EventEvidence(event_id=shared.id, document_id=a.id),
        EventEvidence(event_id=shared.id, document_id=b.id),
        EventEvidence(event_id=solo.id, document_id=lone.id),
    ])
    db_session.commit()

    score_sources(db_session, subject)

    rows = {r.key: r for r in db_session.query(SourceCredibility).all()}
    assert rows["corroborated-a.example"].corroboration_rate == 1.0
    assert rows["lonely.example"].corroboration_rate == 0.0


def test_unseen_sources_fall_back_to_their_type_prior(db_session):
    scores = credibility_for(db_session, ["never-seen.go.ke", "never-seen.blogspot.com"])
    assert scores["never-seen.go.ke"] > scores["never-seen.blogspot.com"]


def test_no_sources_is_a_no_op(db_session):
    subject = _subject(db_session, "Empty Probe")
    assert score_sources(db_session, subject)["scored"] == 0
