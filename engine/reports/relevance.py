"""Is this document about the intersection we were asked about, at all?

A live "senate × forestry" mapping analysed 370 documents and produced nothing.
The evidence sample was New Jersey, Hawaii, Alaska, Georgia, Ontario, Oregon —
one Kenyan item in fifteen. Every downstream section was empty, correctly: the
analysts were asked about a Kenyan senate and handed American local news.

Three things caused it and this module addresses the first two:

  1. The queries were `"senate" "forestry"` — two generic English nouns with no
     geography. For a named person ("Rigathi Gachagua") the name anchors the
     search by itself. For a generic institution it does not, and the whole
     English-speaking world's coverage comes back.

  2. Freshly acquired items were unioned into the corpus AFTER the relevance
     gate ran, so nothing acquired in the run was ever gated.

This is a cheap deterministic check, run before any model reads anything. It is
not a judgement about quality — it asks only whether both halves of the
intersection are present and whether the item belongs to the market we cover.
A model pass costs money and minutes; this costs microseconds, and the material
it removes is material no analyst could have used.
"""

from __future__ import annotations

import re

# The market this product covers. Anchoring is only applied to generic terms —
# a distinctive name never needs it and would be harmed by it.
MARKET_TERMS = (
    "kenya", "kenyan", "nairobi", "mombasa", "kisumu", "nakuru", "eldoret",
    "bungoma", "kakamega", "machakos", "kiambu", "meru", "nyeri", "kitale",
    "county assembly", "ke.", ".co.ke", "east africa", "nyanza", "rift valley",
)

# Places whose presence is strong evidence the item is NOT about our market.
# Used only to explain a rejection, never on its own to reject.
FOREIGN_MARKERS = (
    "new jersey", "hawaii", "alaska", "georgia state", "ontario", "oregon",
    "washington d.c.", "capitol hill", "westminster", "canberra", "ottawa",
    "queensland", "new south wales", "u.s. senate", "us senate", "state senate",
)

# Terms so common that on their own they anchor nothing. An intersection built
# only from these needs a market anchor to mean anything.
GENERIC_TERMS = frozenset("""
senate parliament assembly ministry government cabinet council commission
authority board committee court judiciary police army forestry agriculture
health education housing finance treasury energy water transport mining
land lands environment climate youth women trade industry tourism
""".split())

_WORD = re.compile(r"[a-z0-9']+")


def _norm(text: str) -> str:
    return " ".join(_WORD.findall((text or "").lower()))


def is_generic(term: str) -> bool:
    """True when a term names a category rather than a specific thing.

    "senate" is generic; "Rigathi Gachagua" and "SHA" are not. A generic term
    matches coverage of every senate on earth."""
    # "of"/"the"/"and" carry no signal either way: "Ministry of Health" is as
    # generic as "ministry health", and letting a preposition make it specific
    # would exempt exactly the phrases that need anchoring most.
    filler = {"of", "the", "and", "for", "on", "in", "a", "an"}
    words = [w for w in _WORD.findall((term or "").lower()) if w and w not in filler]
    if not words:
        return True
    return all(w in GENERIC_TERMS for w in words)


def needs_market_anchor(principal: str, issue: str) -> bool:
    """Does this intersection need a geography bolted on to mean anything?

    Only when BOTH halves are generic. One specific term anchors the pair by
    itself — "Rigathi Gachagua" × "forestry" cannot match Oregon, and "senate"
    × "SHA" cannot match New Jersey. Requiring the market word in those cases
    would drop true articles that simply never write "Kenya", which is most of
    the Kenyan press writing for Kenyan readers.
    """
    return is_generic(principal) and is_generic(issue)


def anchor_query(query: str, market: str = "Kenya") -> str:
    """Add the market to a query that would otherwise match the whole world."""
    if market.lower() in query.lower():
        return query
    return f'{query} {market}'


def merge_terms(*groups) -> list[str]:
    """Every distinct term from several sources, longest first.

    Longest first matters: a document mentioning "Okiya Omtatah" should match
    on the full name before the filter falls back to a looser fragment."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for term in group or []:
            key = str(term).strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(str(term).strip())
    return sorted(out, key=len, reverse=True)


def mentions_any(text: str, terms) -> bool:
    haystack = _norm(text)
    return any(_norm(t) and _norm(t) in haystack for t in terms)


def score_document(document: dict, identities, issue_terms,
                   require_market: bool) -> tuple[bool, str]:
    """Keep or drop, with the reason. Never raises.

    The bar is deliberately mechanical: BOTH halves of the intersection must
    appear in the text, and when the intersection is built from generic terms
    the item must also place itself in our market. An analyst cannot say
    anything about "senate × forestry in Kenya" from an article that never
    mentions Kenya — no prompt fixes that, and paying a model to read it is
    money spent to be told nothing.
    """
    text = " ".join(str(document.get(k) or "") for k in ("text", "title", "body"))
    text += " " + str(document.get("source_url") or "")
    if not text.strip():
        return False, "empty document"

    if not mentions_any(text, identities):
        return False, "does not mention the principal"
    if not mentions_any(text, issue_terms):
        return False, "does not mention the issue"
    if require_market and not mentions_any(text, MARKET_TERMS):
        foreign = [m for m in FOREIGN_MARKERS if _norm(m) in _norm(text)]
        return False, (f"not about this market ({foreign[0]})" if foreign
                       else "not about this market")
    return True, "on topic"


#: Which pool a document landed in, and what an analyst may do with it.
POOL_CORE = "core"                # mentions both halves — the intersection itself
POOL_PRINCIPAL = "principal_side"  # the principal, on other things
POOL_ISSUE = "issue_side"          # the issue, without them in it
POOL_OFF = "off_topic"

POOL_MEANING = {
    POOL_CORE: "mentions both the principal and the issue",
    POOL_PRINCIPAL: "about the principal — their record, allies and other fights",
    POOL_ISSUE: "about the issue — its history and the other people in it",
}


def partition_corpus(corpus: list[dict], identities, issue_terms,
                     require_market: bool) -> tuple[dict, dict]:
    """Sort a corpus into what each half of the question can be answered from.

    The old gate was a hard AND: a document had to name the principal AND the
    issue or it was thrown away. On a real question that is nearly everything.
    "Odious debt case by Okiya Omtatah" × "International Monetary Fund" kept
    TWO documents out of hundreds, and five analysts then spent ten minutes
    reading two articles.

    An investigator does not work that way. They read what exists on the
    person, what exists on the issue, and the places the two touch — and the
    third is only findable because they read the first two. The AND is right
    for the intersection itself and wrong for everything around it, so it is
    now a sort rather than a gate: three pools, each labelled with what it can
    honestly support, and nothing silently discarded.
    """
    pools = {POOL_CORE: [], POOL_PRINCIPAL: [], POOL_ISSUE: []}
    reasons: dict[str, int] = {}
    examples: dict[str, str] = {}

    for document in corpus:
        text = " ".join(str(document.get(k) or "") for k in ("text", "title", "body"))
        text += " " + str(document.get("source_url") or "")
        if not text.strip():
            reasons["empty document"] = reasons.get("empty document", 0) + 1
            continue

        has_principal = mentions_any(text, identities)
        has_issue = mentions_any(text, issue_terms)
        if not has_principal and not has_issue:
            reason = "mentions neither the principal nor the issue"
        elif require_market and not mentions_any(text, MARKET_TERMS):
            foreign = [m for m in FOREIGN_MARKERS if _norm(m) in _norm(text)]
            reason = (f"not about this market ({foreign[0]})" if foreign
                      else "not about this market")
        else:
            pool = (POOL_CORE if has_principal and has_issue
                    else POOL_PRINCIPAL if has_principal else POOL_ISSUE)
            document["evidence_pool"] = pool
            pools[pool].append(document)
            continue

        reasons[reason] = reasons.get(reason, 0) + 1
        if reason not in examples:
            examples[reason] = (str(document.get("text") or document.get("title") or ""))[:120]

    kept = sum(len(v) for v in pools.values())
    return pools, {
        "examined": len(corpus),
        "kept": kept,
        "dropped": len(corpus) - kept,
        "pools": {name: len(items) for name, items in pools.items()},
        "reasons": reasons,
        "examples": examples,
        "market_anchored": require_market,
    }


def filter_corpus(corpus: list[dict], identities, issue_terms,
                  require_market: bool) -> tuple[list[dict], dict]:
    """Split a corpus into what an analyst can actually use, and why the rest
    could not be used. Returns (kept, report)."""
    kept: list[dict] = []
    reasons: dict[str, int] = {}
    examples: dict[str, str] = {}
    for document in corpus:
        ok, reason = score_document(document, identities, issue_terms, require_market)
        if ok:
            kept.append(document)
            continue
        reasons[reason] = reasons.get(reason, 0) + 1
        if reason not in examples:
            examples[reason] = (str(document.get("text") or document.get("title") or ""))[:120]
    return kept, {
        "examined": len(corpus),
        "kept": len(kept),
        "dropped": len(corpus) - len(kept),
        "reasons": reasons,
        "examples": examples,
        "market_anchored": require_market,
    }
