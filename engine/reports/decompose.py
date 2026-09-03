"""Turn a free-text query into the parts an investigation can actually use.

An issue map is given two boxes and a person types what they are thinking:

    principal: "Odious debt case by Okiya Omtatah"
    issue:     "International Monetary Fund (IMF)"

Neither is a search term. The first is a claim with a name inside it; the
second is an institution with its acronym in brackets. Matching documents
against those strings whole threw away an interview headlined "Okiya Omtatah:
The Truth Behind Kenya's Debt and the IMF Fall-out" for "not mentioning the
principal" — the single most on-topic item in the entire corpus.

This decomposes both boxes into:

  - `names`      proper nouns and multi-word capitalised spans ("Okiya Omtatah")
  - `acronyms`   bracketed or standalone capitals ("IMF")
  - `keywords`   the topical remainder ("odious debt")
  - `identities` what a document must mention to count as on-topic
  - `queries`    the phrasings worth sending to a search engine

Deterministic, no model call. The decomposition has to work when the provider
is refusing, because everything downstream depends on it — and a stage that
silently degrades to "nothing matched" is the failure this whole system keeps
having.
"""

from __future__ import annotations

import re

# Words that never carry identity on their own.
_STOP = frozenset("""
a an the and or of by for in on at to from with about into over under
case matter issue story report claim allegation allegations affair scandal
saga row dispute probe inquiry investigation deal talks crisis
""".split())

# Bracketed acronyms — "International Monetary Fund (IMF)".
_BRACKETED = re.compile(r"\(([A-Z][A-Za-z0-9&.\-]{1,14})\)")
# Standalone capitals of 2-8 letters — IMF, KRA, SHA, EACC.
_ACRONYM = re.compile(r"\b([A-Z]{2,8})\b")
# A run of capitalised words — a person, an organisation, a place.
_CAPITALISED = re.compile(r"\b([A-Z][a-z’'\-]+(?:\s+(?:of|the|and|for)\s+)?(?:\s+[A-Z][a-z’'\-]+)*)")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9’'\-]+")


def _clean(term: str) -> str:
    return re.sub(r"\s+", " ", term).strip(" .,:;–—-")


def names(text: str) -> list[str]:
    """Multi-word capitalised spans — the people and organisations named."""
    found: list[str] = []
    for match in _CAPITALISED.finditer(text or ""):
        span = _clean(match.group(1))
        words = span.split()
        # One capitalised word is usually just a sentence start; two or more is
        # a name. Keep a single word only when it is not a stopword.
        if len(words) >= 2 or (words and words[0].lower() not in _STOP):
            if len(span) > 2:
                found.append(span)
    # Longest first: "Okiya Omtatah" is more useful than "Okiya".
    return _dedupe(sorted(found, key=len, reverse=True))


def acronyms(text: str) -> list[str]:
    """IMF, KRA, SHA — bracketed or standing alone."""
    found = _BRACKETED.findall(text or "") + _ACRONYM.findall(text or "")
    return _dedupe(f.strip() for f in found if 1 < len(f) <= 8)


def keywords(text: str) -> list[str]:
    """The topical remainder once names and acronyms are removed."""
    stripped = _BRACKETED.sub(" ", text or "")
    for name in names(stripped):
        stripped = stripped.replace(name, " ")
    words = [w.lower() for w in _WORD.findall(stripped)
             if w.lower() not in _STOP and len(w) > 3]
    return _dedupe(words)


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def decompose(text: str) -> dict:
    """One box of free text, broken into its usable parts."""
    text = (text or "").strip()
    found_names = names(text)
    found_acronyms = acronyms(text)
    found_keywords = keywords(text)

    # What a document must mention for this half of the intersection to be
    # present. A name or an acronym is enough; the raw phrase is included so an
    # exact match still counts, but it is never the ONLY thing accepted.
    identities = _dedupe([*found_names, *found_acronyms, text])
    # Keywords alone are too loose to prove identity ("debt" is not Omtatah),
    # so they are search terms only — except when nothing else was found, in
    # which case they are all we have.
    if not found_names and not found_acronyms:
        identities = _dedupe([text, *found_keywords])

    return {
        "raw": text,
        "names": found_names,
        "acronyms": found_acronyms,
        "keywords": found_keywords,
        "identities": identities,
        # Most specific first, so a source that truncates our query list still
        # gets the best ones.
        "queries": _dedupe([text, *found_names, *found_acronyms,
                            " ".join(found_keywords[:3]) if found_keywords else ""]),
    }


def research_dimensions(principal: str, issue: str) -> list[dict]:
    """The angles a deep investigation should cover, as searchable queries.

    An issue map is not one search. "Okiya Omtatah × IMF" is a question about
    a person, an institution, a legal case, a history and a set of opponents —
    and asking a search engine the whole sentence finds none of it.
    """
    left, right = decompose(principal), decompose(issue)
    lead_left = (left["names"] or left["acronyms"] or [left["raw"]])[0]
    lead_right = (right["names"] or right["acronyms"] or [right["raw"]])[0]

    def q(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    dimensions = [
        {"dimension": "intersection",
         "why": "the two halves together — the question as asked",
         "queries": [q(f'"{a}" "{b}"') for a in left["queries"][:3]
                     for b in right["queries"][:3]][:6]},
        {"dimension": "principal_background",
         "why": "who the principal is and what they have done before",
         "queries": [q(f'"{lead_left}" {probe}') for probe in
                     ("profile", "record", "history", "career")]},
        {"dimension": "issue_background",
         "why": "what the issue is, independent of the principal",
         "queries": [q(f'"{lead_right}" {probe}') for probe in
                     ("explained", "background", "history", "Kenya")]},
        {"dimension": "conflict",
         "why": "who is on the other side, and what they say",
         "queries": [q(f'"{lead_left}" "{lead_right}" {probe}') for probe in
                     ("criticism", "opposed", "response", "rebuttal")]},
        {"dimension": "institutions",
         "why": "the bodies with formal power over this",
         "queries": [q(f'"{lead_right}" {probe}') for probe in
                     ("court", "parliament", "ruling", "petition", "audit")]},
        {"dimension": "history",
         "why": "older material a recency-ranked search buries",
         "queries": [q(f'"{lead_left}" "{lead_right}" {probe}') for probe in
                     ("2019", "2020", "2021", "origins", "first")]},
    ]
    for entry in dimensions:
        entry["queries"] = _dedupe(entry["queries"])
    return dimensions
