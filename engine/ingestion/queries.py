"""Kenya-aware query expansion.

Kenyan political discourse mixes English, Swahili and Sheng, and rarely uses
a politician's full formal name — titles ("CS Mbadi", "Waziri wa Fedha"),
surnames and hashtags dominate. A single literal-name query is why earlier
runs undercounted mentions by an order of magnitude.

All variants come from operator-entered fields on the Politician record
(aliases, titles, swahili_terms, tracked_hashtags) — nothing is invented at
query time, so every variant is auditable.
"""

from engine.db.models import Politician

MAX_TEXT_VARIANTS = 12
MAX_HASHTAG_VARIANTS = 8

# Honorifics that commonly prefix a surname in Kenyan media/social posts.
COMMON_HONORIFICS = ["Hon"]

# Subject types that name a *person* — surname/honorific expansion applies.
# Organisations, ministries and businesses are searched by their full name and
# operator-supplied aliases only (a "surname" of an institution is meaningless).
_PERSON_TYPES = {"person", "politician", "individual"}


def surname(name: str) -> str:
    parts = name.strip().split()
    return parts[-1] if parts else name


def text_variants(politician: Politician) -> list[str]:
    """Search-phrase variants for keyword/search endpoints, most-specific first.

    Phrasing adapts to `subject_type`: person-like subjects get surname+title
    and honorific expansion; organisations/ministries/businesses are matched by
    full name + aliases + operator terms, since surname/honorific logic doesn't
    apply to an institution."""
    is_person = (getattr(politician, "subject_type", None) or "politician") in _PERSON_TYPES
    last = surname(politician.name)
    variants: list[str] = [politician.name]
    variants.extend(politician.aliases or [])
    for title in politician.titles or []:
        variants.append(f"{title} {last}" if is_person else title)
        # Standalone Swahili titles ("Waziri wa Fedha") are searchable phrases
        # on their own; single-word English titles ("CS") are not.
        if len(title.split()) > 1:
            variants.append(title)
    if is_person:
        for honorific in COMMON_HONORIFICS:
            variants.append(f"{honorific} {last}")
    variants.extend(politician.swahili_terms or [])

    return _dedupe(variants)[:MAX_TEXT_VARIANTS]


# Comprehensive discovery probes.
#
# A single name query returns whatever is trending today — a narrow slice of a
# subject's public footprint. The goal here is COVERAGE: sweep every dimension of
# a public record, because a decisive detail can sit anywhere, in any kind of
# source, from any period. These probes are therefore deliberately balanced —
# background and achievements as much as legal and adverse material — since
# building only an adverse-media picture is its own way of missing the truth.
#
# Kept as an explicit, auditable list: nothing is invented per-subject at query
# time, so every query that ran can be shown and justified. Genuinely open-ended
# discovery (following leads found in the data) is the Investigator Agent's job.
DISCOVERY_PROBES = [
    # Identity, background, career
    "profile", "biography", "background", "career", "education", "born",
    # Roles, institutions, appointments
    "appointment", "role", "office", "board", "committee", "resignation",
    # Business and financial interests
    "company", "director", "shareholder", "ownership", "business",
    "contract", "tender", "procurement", "funding", "assets", "property",
    # Legal and regulatory record
    "court", "tribunal", "case", "ruling", "investigation", "inquiry", "audit",
    # Adverse signals
    "allegations", "controversy", "corruption", "fraud", "scandal", "charged",
    # Public positions and statements
    "statement", "speech", "interview", "policy", "campaign", "manifesto",
    # People and affiliations
    "family", "associates", "allies", "partnership", "network",
    # Recognition and record
    "award", "achievement", "record", "criticism", "praise",
]

# Search ranks recent material first, so without an explicit nudge the earlier
# record stays invisible. These are period-neutral: they ask for the whole
# timeline rather than assuming any particular decade matters.
DISCOVERY_TIMESPAN_PROBES = [
    "history", "timeline", "past", "former", "early years", "latest", "recent",
]

# High enough that every probe dimension survives the cap. Truncating here would
# silently drop whole categories of evidence — the exact failure this layer
# exists to prevent — so the ceiling is set above the full expansion.
MAX_DISCOVERY_VARIANTS = 80


def discovery_variants(politician: Politician) -> list[str]:
    """Metasearch queries for the discovery layer.

    Built for COVERAGE of a subject's whole public record, since a decisive
    detail can sit in any kind of source from any period:

      Layer 1: identity variants already used everywhere (name/aliases/titles).
      Layer 2: identity x probe — every dimension of the record, balanced across
               background, business, legal, adverse, statements and affiliations.
      Layer 3: identity x timespan — counteracts recency ranking so the earlier
               record is reachable too, without assuming any particular period.

    The subject phrase is quoted so engines match the person, not the words.
    """
    identities = text_variants(politician)[:3]  # most-specific identities only
    primary = identities[0] if identities else politician.name

    variants: list[str] = list(identities)

    # Leads the investigator raised last run come FIRST: they are the specific
    # questions this file actually needs answered, whereas the probe list below
    # is what we ask about everyone. Chasing found leads is what makes
    # successive runs an investigation rather than a repeated sweep.
    for lead in _pending_leads(politician):
        variants.append(lead)

    for probe in DISCOVERY_PROBES:
        variants.append(f'"{primary}" {probe}')
    for probe in DISCOVERY_TIMESPAN_PROBES:
        variants.append(f'"{primary}" {probe}')
    # A second identity broadens recall for subjects known mainly by an alias.
    if len(identities) > 1:
        for probe in DISCOVERY_PROBES[:6]:
            variants.append(f'"{identities[1]}" {probe}')

    return _dedupe(variants)[:MAX_DISCOVERY_VARIANTS]


def hashtag_variants(politician: Politician) -> list[str]:
    """Hashtag terms (no leading '#') for hashtag-search endpoints."""
    last = surname(politician.name)
    variants = [h.lstrip("#") for h in (politician.tracked_hashtags or [])]
    variants.append(politician.name.replace(" ", ""))
    variants.append(last)
    return _dedupe(variants)[:MAX_HASHTAG_VARIANTS]


def _pending_leads(politician: Politician) -> list[str]:
    """Follow-up queries a previous investigator pass recorded on the subject.

    Held in their own column so they never leak into keyword MATCHING.
    """
    return [str(q) for q in (getattr(politician, "investigation_leads", None) or [])]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


# Minimal Swahili function-word list — enough to tag a mention's language for
# reporting/filtering without pulling in a langdetect dependency. Sheng leans
# on the same function words.
_SWAHILI_MARKERS = {
    "na", "ya", "wa", "za", "kwa", "ni", "si", "sana", "lakini", "ama",
    "hii", "huyu", "yake", "wake", "kama", "sasa", "bado", "tu", "pia",
    "amesema", "alisema", "atakuwa", "hakuna", "kuna", "wananchi", "serikali",
    "pesa", "kazi", "leo", "watu", "mtu", "hapa", "sisi", "wewe", "yeye",
}


def detect_language(text: str) -> str:
    words = [w.strip(".,!?;:\"'()").lower() for w in text.split()]
    if not words:
        return "und"
    sw_hits = sum(1 for w in words if w in _SWAHILI_MARKERS)
    if sw_hits / len(words) >= 0.15:
        return "sw"
    return "en"
