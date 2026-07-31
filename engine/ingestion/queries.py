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


# Due-diligence probes. A plain name query returns today's news; these are the
# angles an investigator actually asks about, and they're what surface the old
# tribunal filing or the forgotten contract award that never trends. Kept as an
# explicit, auditable list — nothing is invented per-subject at query time.
DISCOVERY_PROBES = [
    "court", "tribunal", "case", "ruling", "judgment",
    "investigation", "probe", "inquiry", "audit",
    "contract", "tender", "procurement", "award",
    "company", "director", "shareholder", "ownership",
    "wealth", "assets", "property", "land",
    "corruption", "fraud", "scandal", "allegations",
    "arrested", "charged", "sued", "petition",
    "appointment", "resignation", "sacked",
]

# Decade probes pull archived coverage that recency-ranked search hides.
DISCOVERY_ERA_PROBES = ["1990s", "2000s", "history", "profile", "biography", "early career"]

MAX_DISCOVERY_VARIANTS = 40


def discovery_variants(politician: Politician) -> list[str]:
    """Metasearch queries for the discovery layer.

    Layer 1: the identity variants already used everywhere (name/aliases/titles).
    Layer 2: identity x investigative probe — the due-diligence angles.
    Layer 3: identity x era probe — deliberately reaching for old material.

    The subject phrase is quoted so engines match the person, not the words.
    """
    identities = text_variants(politician)[:3]  # most-specific identities only
    primary = identities[0] if identities else politician.name

    variants: list[str] = list(identities)
    for probe in DISCOVERY_PROBES:
        variants.append(f'"{primary}" {probe}')
    for probe in DISCOVERY_ERA_PROBES:
        variants.append(f'"{primary}" {probe}')
    # A second identity broadens recall for subjects known by an alias.
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
