"""Names in the report that appear nowhere in the sources.

The defence against invention was a prompt instruction — GROUNDING_RULES, told
to every analyst — plus quote validation and a verification pass. Quotes are
checked against their ref, so a fabricated quote is dropped. Prose is not. An
analyst can write any sentence it likes into the executive brief and nothing
mechanical ever looks at it.

That held up while the backend was the model the prompts were tuned against.
On a cheaper one it does not, and this file's own codebase already knows the
rule: *a prompt instruction is not a guarantee* — the sentence next to
`style_check.annotate`, which exists for exactly this reason.

So check the one thing that can be checked without a model. If a report about
Musalia Mudavadi names "Peter", and no source in the corpus contains "Peter",
that is not a judgement call about tone or emphasis. It is a name the sources
do not contain, in a document a client is about to read.

Two deliberate limits.

**It only looks at names.** Not claims, not numbers, not reasoning — those
need the verification agent. Names are mechanically checkable and they are
what makes a hallucinating report obvious to a reader at a glance.

**It is conservative.** An entity counts as grounded if any distinctive part
of it appears anywhere in the corpus, so "Mudavadi" grounds against "Musalia
Mudavadi" and "Treasury" against "the National Treasury". Crying wolf on a
real report would be worse than useless: an operator who learns to ignore this
warning has lost the warning, and the genuine case — a name from nowhere — is
flagrant enough to survive a loose test.

Following this codebase's rule, it reports and does not rewrite. Silently
deleting a sentence would leave a gap indistinguishable from a section with
nothing to say, which is the one thing this product refuses to do.
"""

from __future__ import annotations

import re

#: Entity labels worth checking. PERSON and ORG are where invention shows up
#: and where it does the damage; GPE/LOC are skipped because a model naming
#: "Kenya" in a Kenyan report is not evidence of anything.
_CHECKED_TYPES = {"person", "media"}

#: Tokens too common to prove anything on their own. An entity grounded only
#: by one of these is not grounded.
_WEAK_TOKENS = {
    "the", "of", "and", "for", "ltd", "limited", "plc", "inc", "co",
    "group", "holdings", "company", "authority", "ministry", "department",
    "office", "county", "national", "kenya", "kenyan", "african", "east",
    "president", "deputy", "cabinet", "secretary", "senator", "governor",
    "hon", "dr", "mr", "mrs", "ms", "prof", "chief", "justice",
    "news", "media", "tv", "radio", "daily", "nation", "standard", "star",
}

#: Shorter than this proves nothing — initials and particles match everywhere.
_MIN_TOKEN = 4

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]+")

#: A possessive is not part of the name.
#:
#: "Kenya Kwanza's" tokenised to `kwanza's`, which does not appear in a
#: corpus that says "Kenya Kwanza" constantly — so a live report accused
#: itself of inventing the governing coalition.
_POSSESSIVE = re.compile(r"[’']s$")

#: A machine identifier is not a name, whatever an entity model labels it.
#:
#: `0e01efc4-cdc0-4200-aed1-59abce9b926a` yields the four-letter token
#: `abce` — long enough to count as distinctive, and present in no corpus
#: ever written. Five mention ids were reported to a live operator as
#: invented names.
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEXISH = re.compile(r"^[0-9a-f]{8,}$", re.I)

#: A code identifier is not a name either. `HTTPError` and `SocialCrawl` are
#: this engine's OWN vocabulary — an exception class and a connector —
#: reaching prose through a diagnostic field and then being accused of
#: being fabricated people.
_CODE_IDENT = re.compile(r"^(?:[A-Z]{2,}[A-Za-z]*|[A-Z][a-z]+(?:[A-Z][a-z]+)+)$")

#: A fragment ending in a conjunction or preposition is a heading that got
#: cut, not a name: "Dominant Narratives and".
_TRAILING_JOIN = {"and", "or", "of", "for", "in", "on", "with", "to", "the"}


def _tokens(name: str) -> list[str]:
    return [_POSSESSIVE.sub("", t.lower()) for t in _TOKEN.findall(name or "")]


def is_checkable(name: str) -> tuple[bool, str]:
    """Can this candidate be judged at all?

    Returns the reason, because the founding rule applies to this file as
    much as to any report section: "we could not check this" and "this was
    invented" must never render the same. A live report listed fifteen
    ungrounded names, every one of them a machine identifier, this engine's
    own vocabulary, a heading fragment or a possessive — and told the
    operator not to send a clean report to a client.
    """
    text = (name or "").strip()
    if not text:
        return False, "empty"
    if _UUID.search(text) or _HEXISH.match(text.replace("-", "")):
        return False, "machine identifier"
    words = text.split()
    if len(words) == 1 and _CODE_IDENT.match(text):
        return False, "code identifier"
    if words[-1].lower() in _TRAILING_JOIN:
        return False, "sentence fragment"
    return True, ""


def is_accusable(name: str) -> bool:
    """May an UNGROUNDED result for this name be reported as an invention?

    Single-word candidates come only from the entity model — the
    capitalised-pair fallback requires two, by design, because every
    sentence starts with a capital.

    They cannot simply be excluded: "Treasury", "Parliament" and "Mudavadi"
    are real, single-word, and worth checking, and dropping them would make
    the entity model no more thorough than the pattern it is recommended
    over.

    So the asymmetry is the answer, because the two errors are not equal.
    A single word that IS in the corpus is a check that passed, and costs
    nothing. A single word that is NOT could be an invented person or it
    could be "Videos" at the start of a sentence — and accusing a report of
    fabricating "Recognition" is how an operator learns to ignore the one
    banner that matters.

    Checked either way; accused only with corroboration.
    """
    return len((name or "").split()) > 1


def _distinctive(name: str) -> list[str]:
    """The parts of a name that would actually prove a match."""
    return [t for t in _tokens(name)
            if len(t) >= _MIN_TOKEN and t not in _WEAK_TOKENS]


def corpus_haystack(mentions: list[dict]) -> str:
    """One lowercase blob of everything the sources actually said.

    Built from every text-bearing field rather than a chosen one: a name can
    appear in a headline, a body, an author handle or a URL slug, and missing
    it in any of those would raise a false alarm against a real report.
    """
    parts: list[str] = []
    for mention in mentions or []:
        if not isinstance(mention, dict):
            continue
        for key in ("text", "headline", "title", "body", "content",
                    "author", "author_handle", "handle", "source_url", "platform"):
            value = mention.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
        raw = mention.get("raw_payload")
        if isinstance(raw, dict):
            for value in raw.values():
                if isinstance(value, str) and value:
                    parts.append(value)
    return " ".join(parts).lower()


def is_grounded(name: str, haystack: str) -> bool:
    """Does any distinctive part of this name appear in the sources?

    Deliberately loose. "Mudavadi" grounds against "Musalia Mudavadi";
    "Treasury" against "the National Treasury". The failure this catches is a
    name with no presence in the corpus at all.
    """
    parts = _distinctive(name)
    if not parts:
        return True          # nothing distinctive to check — do not accuse
    return any(part in haystack for part in parts)


#: Keys whose values are not prose an analyst wrote.
_SKIP_KEYS = {
    "ref", "refs", "id", "url", "source_url", "link", "href", "platform",
    "handle", "author", "username", "date", "posted_at", "published_at",
    "kind", "type", "stance", "confidence", "label", "outlets",
}


def _prose(payload, out: list[str], key: str | None = None) -> None:
    """Every string an analyst wrote, at any depth.

    A hand-written list of fields is what let raw citations leak for three
    rounds in this codebase; the same mistake would make this check miss the
    section that happens to be hallucinating.
    """
    if isinstance(payload, str):
        if key not in _SKIP_KEYS and not key_is_citation(key):
            out.append(payload)
    elif isinstance(payload, dict):
        for k, v in payload.items():
            _prose(v, out, k)
    elif isinstance(payload, list):
        for item in payload:
            _prose(item, out, key)


def key_is_citation(key: str | None) -> bool:
    return bool(key) and key.endswith("_citations")


def check(payload: dict, mentions: list[dict]) -> dict:
    """Which names in this report do the sources not contain?

    Returns a disclosure, not a verdict:
        {"checked": 23, "ungrounded": ["Peter"], "rate": 0.04}

    An empty `ungrounded` is a real result and is reported as such — "we
    looked and found none" is different from "nobody looked", and this
    codebase does not let those two render the same.
    """
    haystack = corpus_haystack(mentions)
    if not haystack:
        # No corpus to check against. Saying "0 ungrounded" here would claim a
        # clean bill of health from an examination that never happened.
        return {"checked": 0, "ungrounded": [], "rate": 0.0,
                "skipped": "no corpus text to check against"}

    strings: list[str] = []
    _prose(payload, strings)

    seen, how, why = _names_in(strings)

    # Judge only what can be judged, and say how much was set aside.
    judgeable, set_aside = {}, {}
    for key, name in seen.items():
        # NOT `why`: that name already holds the extractor's reason from
        # `_names_in` three lines up, and reusing it here erased it — so
        # `method_reason` vanished and the page went back to guessing why
        # the cheap extractor had run, which is the exact failure this
        # function's own comments were written about.
        ok, verdict = is_checkable(name)
        (judgeable if ok else set_aside)[key] = (name, verdict)

    ungrounded = sorted({name for name, _ in judgeable.values()
                         if not is_grounded(name, haystack)
                         and is_accusable(name)})
    # Checked, found absent, and not corroborated enough to accuse. Counted
    # so the number is not silently different from the one above it.
    unverified = sorted({name for name, _ in judgeable.values()
                         if not is_grounded(name, haystack)
                         and not is_accusable(name)})
    checked = len(judgeable)
    result = {
        "checked": checked,
        "ungrounded": ungrounded,
        "rate": round(len(ungrounded) / checked, 3) if checked else 0.0,
        # Which extractor ran. A thorough pass and a cheap one finding nothing
        # are different results, and an operator comparing two reports needs
        # to know which they are looking at.
        "method": how,
        # Single words the corpus does not contain. Not an accusation —
        # "Videos" at the start of a sentence looks exactly like this — but
        # not hidden either.
        "unverified_single_words": unverified,
        # Candidates that are not names at all. Reported rather than
        # dropped in silence: a reader comparing "300 checked" between two
        # runs is entitled to know what was excluded and why.
        "not_checkable": len(set_aside),
        "not_checkable_reasons": sorted({v for _, v in set_aside.values()}),
    }
    # And WHY, when it was the cheap one. The page used to guess, and a guess
    # that names the wrong cause is worse than saying nothing: it sends the
    # reader to fix something that is already correct.
    if why:
        result["method_reason"] = why
    return result


#: A capitalised multi-word name: "Peter Kamau", "National Treasury".
#:
#: Two words minimum, deliberately. Every sentence begins with a capital, so
#: treating a lone capitalised token as a name would have the check accusing
#: the report of inventing "Tuesday" — and a banner that cries wolf is a
#: banner an operator learns to ignore, which loses the real case. The cost
#: is that a report referring only to "Mudavadi", never "Musalia Mudavadi",
#: contributes no candidates. That errs toward checking fewer names rather
#: than toward false alarms, which is the requirement here.
_NAME_PAIR = re.compile(r"\b([A-Z][a-z’'-]{2,})(?:\s+([A-Z][a-z’'-]{2,})){1,3}\b")


def _names_in(strings: list[str]) -> tuple[dict[str, str], str, str]:
    """Candidate names from the report's prose, however we can get them.

    spaCy's NER is the better extractor, but `settings.low_memory` replaces
    the pipeline with a blank one that has no `ner` pipe at all — and that is
    the DEFAULT. A check that quietly did nothing on a default deployment
    would be worse than no check, because the report would carry a clean
    grounding result that nobody had earned.

    Returns the reason as well as the method, and that third value is the
    point of this function's shape.

    This used to swallow every failure into a bare `except: pass` and report
    "capitalised-names" with no explanation, so the page had to GUESS why and
    guessed the common case: "set LOW_MEMORY=false for the fuller entity
    check". Told to an operator who had already set it, that is a dead end
    dressed as a fix — the same mistake as sending somebody to a settings
    menu that could not exist. There are three different causes with three
    different remedies, and reporting the wrong one costs an evening.
    """
    seen: dict[str, str] = {}
    why = ""
    try:
        from engine.config import settings
        from engine.processing.entities import get_nlp

        pipeline = get_nlp()
        if "ner" in pipeline.pipe_names:
            from engine.processing.entities import extract_standard_entities

            for text in strings:
                if len(text) < 12:
                    continue
                for entity in extract_standard_entities(text):
                    if entity.get("type") in _CHECKED_TYPES:
                        name = (entity.get("name") or "").strip()
                        if name:
                            seen.setdefault(name.lower(), name)
            return seen, "ner", ""

        # A blank pipeline, and the two ways to get one are not the same
        # problem. One is a setting the operator controls; the other is a
        # missing model in the image, which no amount of changing settings
        # will fix.
        if settings.low_memory:
            why = ("LOW_MEMORY is on, which replaces the entity model with a "
                   "blank pipeline. Set LOW_MEMORY=false on an instance with "
                   "~1GB to use the fuller check.")
        else:
            why = ("LOW_MEMORY is off, so this is not a settings problem: the "
                   "en_core_web_sm model is not loadable in this deployment. "
                   "The image builds it with `python -m spacy download "
                   "en_core_web_sm` — check that step ran.")
    except Exception as exc:  # noqa: BLE001 — a check must never break a report
        why = f"the entity model could not be loaded: {type(exc).__name__}: {exc}"[:240]

    for text in strings:
        if len(text) < 12:
            continue
        for match in _NAME_PAIR.finditer(text):
            name = match.group(0).strip()
            if _distinctive(name):
                seen.setdefault(name.lower(), name)
    return seen, "capitalised-names", why
