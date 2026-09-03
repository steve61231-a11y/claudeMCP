"""What an issue map says when every model call fails.

A run that collects three hundred documents, spends twenty minutes, and then
renders "not yet established" in every section is the worst output this system
produces. It is not that no answer exists — the documents are sitting right
there — it is that the only component able to read them refused, and nothing
downstream was willing to say anything on its own.

This is the floor: actors, developments and themes derived from the corpus by
counting and named-entity extraction, with no model involved. It is not as good
as an analyst read and it does not pretend to be — every item it produces is
marked `derived: true` so the page can say where it came from. It exists so
that a reader who waited twenty minutes gets the names, the dates and the
outlets rather than a form with nothing in it.

It fills gaps only. Any section an analyst actually returned is left alone.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

#: Words that top every frequency count in any corpus and name nothing.
_STOP = frozenset("""
the a an and or but of to in on at for with from by as is are was were be been
this that these those it its their his her they he she we you i not no than
then out up down over under about after before new says said say will would
can could may might must should has have had do does did
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def _text(document: dict) -> str:
    # Separated by a full stop, not a space: a title runs straight into a body
    # otherwise, and the join welds the two into one phantom name.
    parts = [str(document.get(k) or "").strip() for k in ("title", "text", "body")]
    return " . ".join(p for p in parts if p)


def _when(document: dict):
    posted = document.get("posted_at")
    if isinstance(posted, datetime):
        return posted
    try:
        return datetime.fromisoformat(str(posted)[:19])
    except (TypeError, ValueError):
        return None


def _evidence(document: dict) -> dict:
    body = (document.get("text") or document.get("title") or "").strip()
    return {"text": body[:240],
            "url": (document.get("raw_payload") or {}).get("url")
                   or document.get("source_url") or ""}


#: A capitalised run of words, or an acronym of 2-6 letters. This is how names
#: are found when spaCy's NER model is not installed — which is the case on
#: this deploy, so `extract_standard_entities` has been returning [] to every
#: caller. News prose capitalises the things this needs to find.
# "of" is allowed inside a name ("Ministry of Health"); "and"/"the" are not,
# or "National Treasury and Njuguna Ndungu" becomes one actor.
_PROPER = re.compile(r"\b(?:[A-Z][a-z'\-]{1,}(?:\s+(?:of\s+)?[A-Z][a-z'\-]{1,}){0,4}|[A-Z]{2,6})\b")

#: Matching across a full stop welds the last name of one sentence to the first
#: of the next — "Parliament The National Treasury".
_SENTENCE = re.compile(r"(?<=[.!?;:])\s+|\n+")

#: Capitalised words that begin sentences or head boilerplate and name nobody.
_NOT_A_NAME = frozenset("""
the this that these those a an and or but of to in on at for with from by as
it he she they we you i not no new news says said say will would can could may
might must should has have had do does did after before over under about more
most kenya kenyan nairobi read also watch video photo file opinion editorial
monday tuesday wednesday thursday friday saturday sunday january february march
april may june july august september october november december
""".split())


def _proper_names(text: str) -> list[str]:
    """Names a human would recognise as names, without a model.

    Single capitalised words are dropped unless they are acronyms: "Parliament"
    on its own is a place, "IMF" is an actor, and "National Treasury" is what
    this is for.
    """
    found: list[str] = []
    candidates: list[str] = []
    for sentence in _SENTENCE.split(text or ""):
        # A sentence-initial word is capitalised by grammar rather than by
        # being a name, but it is a SINGLE word, and single words are already
        # dropped below unless they are acronyms. Chopping the first character
        # to defend against it cost "Katiba Institute" its K.
        candidates.extend(_PROPER.findall(sentence.strip()))
    for candidate in candidates:
        name = " ".join(candidate.split())
        words = name.split()
        lowered = [w.lower() for w in words]
        # "of" joins a name ("Ministry of Health"); it never starts or ends one.
        interior = set(lowered[1:-1]) if len(words) > 2 else set()
        if any(w in _NOT_A_NAME for w in lowered
               if not (w == "of" and w in interior)):
            # Split on the boilerplate word and keep the longest surviving run,
            # rather than losing the whole name or keeping the weld. "Parliament
            # The National Treasury" is two names with a joint in the middle.
            runs, current = [], []
            for word in words:
                if word.lower() in _NOT_A_NAME and not (
                        word.lower() == "of" and current and word.lower() in interior):
                    if current:
                        runs.append(current)
                    current = []
                else:
                    current.append(word)
            if current:
                runs.append(current)
            words = max(runs, key=len) if runs else []
            name = " ".join(words)
        if not name:
            continue
        if len(words) == 1 and not (name.isupper() and 2 <= len(name) <= 6):
            continue
        if len(name) < 3:
            continue
        found.append(name)
    return found


def actors(corpus: list[dict], principal: str, limit: int = 25) -> list[dict]:
    """People and organisations the corpus actually names, by how often.

    Frequency is a weak proxy for influence and is labelled as one. It is a
    far better answer than an empty list, which reads as "nobody is involved".
    """
    try:
        from engine.processing.entities import extract_standard_entities
    except Exception:  # noqa: BLE001
        extract_standard_entities = None

    counts: Counter = Counter()
    kinds: dict[str, str] = {}
    seen: dict[str, dict] = {}
    principal_lower = (principal or "").lower()

    for document in corpus[:200]:
        body = _text(document)[:4000]
        if not body.strip():
            continue
        found = []
        if extract_standard_entities is not None:
            try:
                found = [e for e in extract_standard_entities(body)
                         if e.get("type") in ("person", "media")]
            except Exception:  # noqa: BLE001 — extraction is a bonus, never a cost
                found = []
        # NER returns nothing when its model is absent, which is not a reason
        # to hand the reader an empty actor list.
        if not found:
            found = [{"name": n, "type": "media"} for n in _proper_names(body)]

        for entity in found:
            name = " ".join(str(entity.get("name") or "").split())
            # Either way round: "Senator Okiya Omtatah" is the principal even
            # though the principal's name does not contain it.
            lowered_name = name.lower()
            if len(name) < 3 or (principal_lower and (
                    lowered_name in principal_lower or principal_lower in lowered_name)):
                continue
            counts[name] += 1
            kinds.setdefault(name, "person" if entity.get("type") == "person"
                             else "organization")
            seen.setdefault(name, document)

    top = counts.most_common(limit)
    if not top:
        return []
    highest = top[0][1] or 1
    return [
        {
            "name": name,
            "entity_type": kinds.get(name, "organization"),
            "position": "neutral",
            "influence": max(5, round(100 * count / highest)),
            "relation": (f"Named in {count} of the documents collected. This actor was "
                         "extracted from the text, not read by an analyst — the stance "
                         "and the role are not established."),
            "quotes": [_evidence(seen[name])],
            "derived": True,
        }
        for name, count in top
    ]


def timeline(corpus: list[dict], limit: int = 25) -> list[dict]:
    """One development per dated document, newest last. The headline is the
    claim; using it verbatim invents nothing."""
    dated = []
    for document in corpus:
        when = _when(document)
        if when is None:
            continue
        headline = " ".join(str(document.get("title") or document.get("text") or "").split())
        if len(headline) < 12:
            continue
        dated.append((when, headline[:220], document))

    dated.sort(key=lambda row: row[0])
    # Newest matter most, but the reader wants them in order once chosen.
    chosen = dated[-limit:]
    return [
        {"date": when.date().isoformat(), "event": headline,
         "sources": 1, "quotes": [_evidence(document)], "derived": True}
        for when, headline, document in chosen
    ]


def themes(corpus: list[dict], principal: str, issue: str, limit: int = 8) -> list[dict]:
    """The words the coverage actually turns on. Weak, honest, and not empty."""
    stop = set(_STOP)
    for phrase in (principal, issue):
        stop.update(w.lower() for w in _WORD.findall(str(phrase or "")))

    counts: Counter = Counter()
    example: dict[str, dict] = {}
    for document in corpus[:400]:
        words = {w.lower() for w in _WORD.findall(_text(document)) if len(w) > 4}
        for word in words - stop:
            counts[word] += 1
            example.setdefault(word, document)

    common = [(w, c) for w, c in counts.most_common(limit * 3) if c > 1][:limit]
    if not common:
        return []
    highest = common[0][1] or 1
    return [
        {"narrative": word.title(),
         "framing": f"Appears in {count} of the documents collected.",
         "strength": max(5, round(100 * count / highest)),
         "detail": ("A recurring term in the coverage, counted rather than read. "
                    "It marks where the material concentrates; it is not a storyline "
                    "an analyst has verified."),
         "quotes": [_evidence(example[word])],
         "derived": True}
        for word, count in common
    ]


def fill(analysis: dict, corpus: list[dict], principal: str, issue: str) -> dict:
    """Fill only what the analysts did not return, and say which is which."""
    analysis = dict(analysis or {})
    derived: list[str] = []

    if not analysis.get("key_actors"):
        found = actors(corpus, principal)
        if found:
            analysis["key_actors"] = found
            derived.append("key_actors")
    if not analysis.get("timeline"):
        found = timeline(corpus)
        if found:
            analysis["timeline"] = found
            derived.append("timeline")
    if not analysis.get("linking_narratives"):
        found = themes(corpus, principal, issue)
        if found:
            analysis["linking_narratives"] = found
            derived.append("linking_narratives")

    if derived:
        analysis["derived_sections"] = derived
    return analysis
