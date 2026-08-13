"""Source credibility — not all corroboration is worth the same.

"Three sources say so" is meaningless until you know which three. Three
established newsrooms reporting independently is strong; three anonymous blogs
reposting each other is one rumour wearing a disguise. Without this distinction
a system rewards whatever is loudest, which in practice means whoever is most
motivated to repeat themselves.

Scores are built from four components, each observable rather than assumed:

  source type    — an established outlet, an official record, a personal blog
                   and an anonymous social account are not equivalent priors,
  independence   — an outlet that only ever echoes others adds little,
  corroboration  — how often this source's claims are borne out by others,
  history        — how much we have actually seen from it (a brand-new domain
                   with one story earns less trust than a long track record).

Every score keeps its component breakdown, so "why is this trusted?" always has
an answer. Nothing here is a permanent judgement of an outlet — it is a running
observation that updates as the corpus grows.
"""

import re
from datetime import datetime

from sqlalchemy import func

from engine.db.models import Document, Event, EventEvidence, RawMention, SourceCredibility

# Priors by source character. These are starting points that observation then
# moves — not a fixed ranking of publishers.
TYPE_PRIORS = {
    "official": 0.85,     # government/court/registry records
    "mainstream": 0.75,   # established newsrooms
    "digital": 0.6,       # online-native outlets
    "wire": 0.8,          # news agencies
    "encyclopedia": 0.7,  # Wikipedia and similar
    "blog": 0.4,
    "social": 0.35,       # individual social accounts
    "aggregator": 0.3,
    "unknown": 0.45,
}

# Domain hints. Kept explicit and small: a hand-tuned allowlist of "trusted"
# publishers would bake in bias, so this only recognises structural signals
# (government registries, wire services, known encyclopedias).
_OFFICIAL_PATTERNS = (r"\.go\.ke$", r"\.gov(\.|$)", r"\.gob\.", r"parliament", r"judiciary", r"\.court")
_WIRE_PATTERNS = (r"reuters", r"apnews", r"afp\.com", r"bloomberg")
_ENCYCLOPEDIA_PATTERNS = (r"wikipedia", r"britannica")
_BLOG_PATTERNS = (r"blogspot", r"wordpress\.com", r"medium\.com", r"substack")
_SOCIAL_PLATFORMS = {"twitter", "x", "facebook", "instagram", "tiktok", "reddit", "youtube", "linkedin"}


def classify_source(key: str, platform: str | None = None) -> str:
    """Structural classification of a source from its identifier."""
    identifier = (key or "").lower()
    if platform and platform.lower() in _SOCIAL_PLATFORMS:
        return "social"
    if identifier.startswith("@"):
        return "social"
    for pattern in _OFFICIAL_PATTERNS:
        if re.search(pattern, identifier):
            return "official"
    for pattern in _WIRE_PATTERNS:
        if re.search(pattern, identifier):
            return "wire"
    for pattern in _ENCYCLOPEDIA_PATTERNS:
        if re.search(pattern, identifier):
            return "encyclopedia"
    for pattern in _BLOG_PATTERNS:
        if re.search(pattern, identifier):
            return "blog"
    if identifier and "." in identifier:
        return "digital"
    return "unknown"


def _history_factor(observations: int) -> float:
    """How much weight the track record itself earns.

    One story from an unknown domain is not evidence of reliability; a long
    record is. This saturates rather than growing without limit — volume alone
    shouldn't buy unlimited trust.
    """
    if observations <= 1:
        return 0.0
    if observations < 5:
        return 0.25
    if observations < 20:
        return 0.5
    if observations < 100:
        return 0.75
    return 1.0


def compute_score(source_type: str, corroboration_rate: float, observations: int,
                  independence: float = 0.5) -> tuple[float, dict]:
    """Blend the components into one score, keeping the breakdown.

    The prior dominates until there is enough observed behaviour to move it —
    which is the honest position when we've barely seen a source.
    """
    prior = TYPE_PRIORS.get(source_type, TYPE_PRIORS["unknown"])
    history = _history_factor(observations)
    # Observed behaviour is blended in proportion to how much we've observed.
    observed = 0.5 * corroboration_rate + 0.5 * independence
    score = prior * (1 - 0.4 * history) + observed * (0.4 * history)
    score = max(0.05, min(0.98, score))
    return round(score, 3), {
        "prior": prior,
        "type": source_type,
        "corroboration_rate": round(corroboration_rate, 3),
        "independence": round(independence, 3),
        "observations": observations,
        "history_weight": history,
    }


def score_sources(db, politician) -> dict:
    """Observe every source behind this subject's corpus and score it.

    Corroboration is measured through events: a source whose reported events are
    also reported elsewhere is being borne out; one whose stories nobody else
    carries is either exclusive or unreliable, and the score stays cautious
    because we cannot yet tell which.
    """
    counts: dict[str, dict] = {}

    for domain, total in (
        db.query(Document.domain, func.count(Document.id))
        .filter(Document.politician_id == politician.id, Document.domain.isnot(None))
        .group_by(Document.domain)
        .all()
    ):
        counts.setdefault(domain.lower(), {"observations": 0, "platform": None})["observations"] += int(total)

    for platform, handle, total in (
        db.query(RawMention.platform, RawMention.author_handle, func.count(RawMention.id))
        .filter(RawMention.politician_id == politician.id)
        .group_by(RawMention.platform, RawMention.author_handle)
        .all()
    ):
        if not handle:
            continue
        key = f"@{handle.lower()}"
        entry = counts.setdefault(key, {"observations": 0, "platform": platform})
        entry["observations"] += int(total)
        entry["platform"] = platform

    if not counts:
        return {"scored": 0}

    # Corroboration per source: of the events this source evidenced, how many
    # were independently reported by someone else.
    corroborated: dict[str, list[int]] = {}
    evidence_rows = (
        db.query(EventEvidence, Document.domain)
        .outerjoin(Document, Document.id == EventEvidence.document_id)
        .join(Event, Event.id == EventEvidence.event_id)
        .filter(Event.politician_id == politician.id)
        .all()
    )
    event_domains: dict[str, set[str]] = {}
    for row, domain in evidence_rows:
        if domain:
            event_domains.setdefault(row.event_id, set()).add(domain.lower())
    for row, domain in evidence_rows:
        if not domain:
            continue
        others = event_domains.get(row.event_id, set()) - {domain.lower()}
        corroborated.setdefault(domain.lower(), []).append(1 if others else 0)

    now = datetime.utcnow()
    scored = 0
    for key, info in counts.items():
        source_type = classify_source(key, info.get("platform"))
        outcomes = corroborated.get(key, [])
        rate = (sum(outcomes) / len(outcomes)) if outcomes else 0.5
        # A source that never appears alongside others is not independent
        # corroboration for anything — treat unknown as neutral, not good.
        independence = 0.5 if not outcomes else min(1.0, 0.3 + 0.7 * rate)
        score, components = compute_score(source_type, rate, info["observations"], independence)

        record = db.query(SourceCredibility).filter_by(key=key).first()
        if record is None:
            record = SourceCredibility(key=key)
            db.add(record)
        record.source_type = source_type
        record.score = score
        record.components = components
        record.corroboration_rate = rate
        record.observations = info["observations"]
        record.updated_at = now
        scored += 1

    db.commit()
    return {"scored": scored}


def credibility_for(db, keys: list[str]) -> dict[str, float]:
    """Look up scores for sources, defaulting to the type prior when unseen."""
    if not keys:
        return {}
    normalized = [k.lower() for k in keys if k]
    rows = db.query(SourceCredibility).filter(SourceCredibility.key.in_(normalized)).all()
    found = {r.key: float(r.score or 0.5) for r in rows}
    for key in normalized:
        if key not in found:
            found[key] = TYPE_PRIORS.get(classify_source(key), TYPE_PRIORS["unknown"])
    return found


def weighted_confidence(base: float, source_keys: list[str], scores: dict[str, float]) -> float:
    """Adjust a claim's confidence by the quality of what backs it.

    Independent sources still matter most, but three weak sources should not
    outrank two strong ones — so the mean credibility of the backing sources
    modulates the corroboration-derived figure.
    """
    if not source_keys:
        return round(base * 0.7, 3)
    values = [scores.get(k.lower(), 0.45) for k in source_keys]
    mean = sum(values) / len(values)
    # Centre on 0.6: better-than-typical sources lift, weaker ones pull down.
    adjusted = base * (0.7 + 0.5 * mean)
    return round(max(0.05, min(0.99, adjusted)), 3)
