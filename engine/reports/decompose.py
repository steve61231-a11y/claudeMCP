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
# A run of capitalised words — a person, an organisation, a place. A single
# capital may follow a word ("Gen Z"), so the name is not cut to "Gen".
_CAPITALISED = re.compile(r"\b([A-Z][a-z’'\-]+(?:\s+(?:of|the|and|for)\s+)?(?:\s+(?:[A-Z][a-z’'\-]+|[A-Z]\b))*)")
# A brand written with a lowercase prefix — e-Citizen, i-Tax, M-Pesa. Read
# as capitalised words alone, "e-Citizen" became "Citizen", which matched
# every Citizen TV story in the country.
_PREFIXED = re.compile(r"(?<![\w-])([a-zA-Z]{1,2}-[A-Z][A-Za-z’'\-]+)")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9’'\-]+")


#: Words that name a place or a people, not an issue or an actor. "Kenyan"
#: on its own matched every Kenyan document as "about the issue"; a name
#: made only of these and generic institution words ("Kenyan Court") is a
#: description, not the thing a source would print as a name.
_DEMONYMS = frozenset("""
kenya kenyan kenyans african africa east west international national county
""".split())
_GENERIC_INSTITUTION = frozenset("""
court courts government state parliament senate assembly ministry cabinet
office president presidency commission authority board agency council
department judiciary high supreme appeal public
""".split())

#: An issue with this many words or more is a sentence someone typed, not a
#: name a source prints: "Kenyan Court stopping the Kenyan government's sale
#: of its Safaricom shares to Vodacom" was searched whole and found nothing.
SENTENCE_WORDS = 7
MAX_SEARCH_TERMS = 6


def _clean(term: str) -> str:
    return re.sub(r"\s+", " ", term).strip(" .,:;–—-")


def _common_words() -> frozenset:
    """The 20,000 most frequent English words (engine/data/common_english.txt)
    — the test of whether a lone capitalised word is a name or sentence case.
    "Housing levy" is about housing, "Shakahola massacre" is about
    Shakahola, and only a frequency list tells the two apart without a
    model. Missing file: an empty set, so every lone word is treated as a
    possible name, as before."""
    global _COMMON
    if _COMMON is None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "data" / "common_english.txt"
        try:
            _COMMON = frozenset(line.strip() for line in path.read_text().splitlines()
                                if line.strip() and not line.startswith("#"))
        except OSError:
            _COMMON = frozenset()
    return _COMMON


_COMMON: frozenset | None = None


def is_common_word(word: str) -> bool:
    return word.lower().strip("’'") in _common_words()


def names(text: str) -> list[str]:
    """Multi-word capitalised spans — the people and organisations named."""
    found: list[str] = []
    prefixed = [m.group(1) for m in _PREFIXED.finditer(text or "")]
    for brand in prefixed:
        found.append(brand)
        text = text.replace(brand, " ")
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


def _words_of(name: str) -> list[str]:
    return [w.lower().strip("’'") for w in _WORD.findall(name)]


def is_descriptive(name: str) -> bool:
    """A name made only of demonyms and generic institution words."""
    words = [w for w in _words_of(name) if w not in _STOP]
    return bool(words) and all(w in _DEMONYMS or w in _GENERIC_INSTITUTION
                               or w.rstrip("s") in _GENERIC_INSTITUTION for w in words)


def is_sentence(text: str) -> bool:
    return len(_WORD.findall(text or "")) >= SENTENCE_WORDS


def anchors(text: str) -> list[str]:
    """The names a source would actually print, most distinctive first:
    names with a word of their own (Safaricom, Vodacom, Okiya Omtatah), then
    acronyms, then descriptive names (National Treasury). A demonym alone
    is never one."""
    # A lone capitalised word that is also an everyday English word —
    # "Housing", "Citizen", "Karen" — is sentence case or too common to name
    # anything; a lone rare word ("Safaricom", "Shakahola") is a name.
    found = [n for n in names(text)
             if " " in n or "-" in n or not is_common_word(n)]
    distinctive = [n for n in found if not is_descriptive(n)]
    descriptive = [n for n in found if is_descriptive(n)
                   and len([w for w in _words_of(n) if w not in _DEMONYMS]) >= 1
                   and not all(w in _DEMONYMS for w in _words_of(n))]
    # A descriptive name that is a single generic word ("Court") says nothing.
    descriptive = [n for n in descriptive if len(_words_of(n)) >= 2
                   and not all(w in _DEMONYMS for w in _words_of(n)[1:])]
    # A descriptive name stands in only when there is nothing more
    # distinctive: "a Kenyan court ruled…" is every court story in the
    # country, so beside "Safaricom" it would let any of them count as the
    # issue.
    specific = _dedupe([*distinctive, *acronyms(text)])
    return specific or _dedupe(descriptive)


def _nounish(word: str) -> bool:
    """A content word a headline would set beside a name: not a stopword,
    not a verb form ("stopping", "nullified"), not a possessive."""
    w = word.lower().strip("’'")
    return (len(w) > 3 and w not in _STOP and w not in _DEMONYMS
            and not w.endswith(("ing", "ed")) and not w.endswith("'s") and word[:1].islower())


def phrases(text: str, anchor_names: list[str]) -> list[str]:
    """Short phrases a headline would carry, built round each anchor from the
    words beside it in the text: the word after it ("Safaricom shares"), and
    the noun in "<noun> of its <anchor>" ("Safaricom sale"). Deterministic,
    no model; a verb or an acronym is never a pairing word."""
    out: list[str] = []
    tokens = _WORD.findall(text or "")
    lowered = [t.lower().strip("’'") for t in tokens]
    for anchor in anchor_names[:3]:
        first = _words_of(anchor)
        if not first or is_descriptive(anchor) or anchor.isupper():
            continue
        n = len(first)
        for i in range(len(lowered) - n + 1):
            if lowered[i:i + n] != first:
                continue
            after = tokens[i + n] if i + n < len(tokens) else ""
            if _nounish(after):
                out.append(f"{anchor} {after}")
            # "<noun> of [its|the|a] <anchor>"
            j = i - 1
            while j >= 0 and lowered[j] in ("its", "the", "a", "an", "his", "her", "their"):
                j -= 1
            if j >= 1 and lowered[j] == "of" and _nounish(tokens[j - 1]):
                out.append(f"{anchor} {tokens[j - 1]}")
            break
    return _dedupe(out)


def content_phrases(text: str) -> list[str]:
    """The runs of content words in a short issue, split at stopwords —
    "land grabbing in Karen" gives "land grabbing"; "university funding
    model" gives itself and "funding model". What a headline prints, where
    the issue as typed is a description no headline uses."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9’'\-]*", text or "")
    runs, current = [], []
    for token in tokens:
        if token.lower() in _STOP or token.lower() in ("in", "on", "at", "to", "of"):
            if current:
                runs.append(current)
            current = []
        else:
            current.append(token)
    if current:
        runs.append(current)
    out = []
    for run in runs:
        if len(run) >= 2:
            out.append(" ".join(run))
        tail = run[-2:]
        if len(run) >= 3 and all(w[:1].islower() and len(w) > 2 for w in tail):
            out.append(" ".join(tail))
    return _dedupe(out)


def search_terms(text: str) -> list[str]:
    """What to put in a search box for this half of the question. A short
    issue as typed comes first ("Finance Bill 2024", "SHA"); a sentence
    never does. Then the anchors and the phrases built round them."""
    text = (text or "").strip()
    found = anchors(text)
    built = phrases(text, found)
    # "the IMF loan" is printed "IMF loan": a leading article is the
    # client's grammar, not the headline's.
    bare = re.sub(r"^(?:the|a|an)\s+", "", text, flags=re.I)
    lead = [] if is_sentence(text) else [bare]
    # A lone capitalised word that opens the box ("Odious debt case…") is
    # sentence case, not a name; it is searched only inside its phrase.
    opener = (_WORD.findall(text) or [""])[0]
    found = [a for a in found if not (a == opener and " " not in a and not a.isupper()
                                      and any(p.startswith(a + " ") for p in built))]
    loose = [] if is_sentence(text) else content_phrases(text)
    terms = _dedupe([*lead, *built, *loose, *found])
    if not terms:
        terms = [text] if text else []
    return terms[:MAX_SEARCH_TERMS]


def decompose(text: str) -> dict:
    """One box of free text, broken into its usable parts."""
    text = (text or "").strip()
    found_names = [n for n in names(text) if not all(w in _DEMONYMS for w in _words_of(n))
                   and (" " in n or "-" in n or not is_common_word(n))]
    found_acronyms = acronyms(text)
    found_keywords = keywords(text)

    # What a document must mention for this half of the intersection to be
    # present. A name or an acronym is enough; the raw phrase is included so an
    # exact match still counts, but it is never the ONLY thing accepted.
    specific = anchors(text)
    identities = _dedupe([*specific, *found_acronyms, text])
    # Keywords alone are too loose to prove identity ("debt" is not Omtatah),
    # so they are search terms only — except when nothing else was found, in
    # which case they are all we have.
    if not specific and not found_acronyms:
        # A lowercase issue — "school fees", "teachers strike" — is its
        # phrase. Its single words were identities too, so "school" or
        # "county" alone counted a document as being about the issue.
        identities = _dedupe([text])

    return {
        "raw": text,
        "names": found_names,
        "acronyms": found_acronyms,
        "keywords": found_keywords,
        "identities": identities,
        "anchors": anchors(text),
        "sentence": is_sentence(text),
        # Most specific first, so a source that truncates our query list still
        # gets the best ones — and never a typed sentence, which no source
        # prints and every source answers with nothing.
        "queries": search_terms(text),
    }


def research_dimensions(principal: str, issue: str, window_start=None,
                        institutions: list[str] | None = None) -> list[dict]:
    """The angles a deep investigation should cover, as searchable queries.

    An issue map is not one search. "Okiya Omtatah × IMF" is a question about
    a person, an institution, a legal case, a history and a set of opponents —
    and asking a search engine the whole sentence finds none of it.
    """
    left, right = decompose(principal), decompose(issue)
    # The anchor — the most distinctive name — not the first capitalised
    # span: "Kenyan Court stopping … Safaricom shares to Vodacom" asked every
    # dimension about "Kenyan Court".
    lead_left = (left["anchors"] or [left["raw"]])[0]
    lead_right = (right["anchors"] or right["queries"] or [right["raw"]])[0]
    # Years BEFORE the window, counted from it. The history dimension asked
    # for 2019, 2020 and 2021 whatever the issue and whenever it was run.
    from datetime import datetime as _dt

    start_year = (window_start or _dt.utcnow()).year
    earlier = [str(start_year - k) for k in (1, 2, 3)]

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
         # The bodies that decide an issue of THIS kind, by the names a
         # source prints (`issue_profile.KINDS`) — a teachers' strike is
         # the TSC's and KNUT's, a forest eviction the Kenya Forest
         # Service's. The generic five were the fallback for every issue.
         "queries": [q(f'"{lead_right}" "{body}"') for body in institutions]
         if institutions else [q(f'"{lead_right}" {probe}') for probe in
                               ("court", "parliament", "ruling", "petition", "audit")]},
        {"dimension": "history",
         "why": "older material a recency-ranked search buries",
         "queries": [q(f'"{lead_left}" "{lead_right}" {probe}') for probe in
                     (*earlier, "origins", "first")]},
    ]
    for entry in dimensions:
        entry["queries"] = _dedupe(entry["queries"])
    return dimensions
