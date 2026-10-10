"""Catch the house-style violations a prompt instruction can't guarantee it
prevented — first person and hedging, specifically, because those are the
two things the client explicitly flagged: "the AI right now is saying 'I
believe this is what is happening', and we don't know. That's not how it
works in this industry."

This never rewrites prose automatically — silently editing a model's output
risks distorting a claim nobody then checks. It flags. The flag travels with
the report so a reader (or an editor) can see exactly where house style was
violated, the same transparency this codebase already gives derived sections
and unresolved citations.
"""

from __future__ import annotations

import re

from engine.reports import prose

# Every one of these SHOULD be structurally impossible after the house-style
# instruction in GROUNDING_RULES — this exists because "should be impossible"
# and "is impossible" are not the same claim, and only one of them is checked.
_FIRST_PERSON = re.compile(
    r"\b(i believe|i think|in my (view|opinion)|it seems to me|i would say|"
    r"i suspect|my (view|opinion|read) is)\b", re.IGNORECASE)

_HEDGES = re.compile(
    r"\b(perhaps|arguably|it could be argued|somewhat|it seems|seemingly|"
    r"may (suggest|indicate)|possibly|presumably)\b", re.IGNORECASE)


# Inflated words, and the plain one to use instead.
#
# The Economist's style guide takes its opening rules from Orwell: never use a
# long word where a short one will do; if it is possible to cut a word out,
# always cut it out; never use jargon where an everyday English equivalent
# exists. The guide's own warning is that long words and borrowed jargon are
# usually covering a lack of thought — which is exactly the failure mode of a
# language model asked to sound authoritative.
#
# This matters here for a specific reason: the reader is an executive deciding
# in minutes. "The subject has been the beneficiary of a significant number of
# procurement awards" and "the subject won many state contracts" carry the same
# information, and only one of them can be read at speed.
#
# Pairs, not a blocklist. Flagging a word without offering the replacement
# produces an editor's complaint rather than an edit.
PLAIN_WORDS = {
    "utilise": "use", "utilize": "use", "utilisation": "use",
    "commence": "begin", "commenced": "began", "commencement": "start",
    "endeavour": "try", "endeavor": "try",
    "facilitate": "help", "facilitated": "helped",
    "leverage": "use", "leveraged": "used",
    "prior to": "before", "subsequent to": "after", "following on from": "after",
    "in order to": "to", "so as to": "to",
    "with regard to": "about", "with respect to": "about", "in relation to": "about",
    "in the event that": "if", "in the event of": "if",
    "at this point in time": "now", "at the present time": "now",
    "a significant number of": "many", "a large number of": "many",
    "the majority of": "most", "a number of": "some",
    "in excess of": "more than", "in the region of": "about",
    "approximately": "about", "circa": "about",
    "terminate": "end", "terminated": "ended",
    "purchase": "buy", "purchased": "bought",
    "permit": "let", "persons": "people",
    "demonstrate": "show", "demonstrates": "shows", "demonstrated": "showed",
    "sufficient": "enough", "insufficient": "too little", "additional": "more",
    "ascertain": "find out", "elucidate": "explain",
    "ameliorate": "improve", "exacerbate": "worsen", "exacerbated": "worsened",
    "promulgate": "issue", "requisite": "needed", "cognisant": "aware",
    "aforementioned": "this", "heretofore": "until now",
    "notwithstanding": "despite", "pursuant to": "under",
    "in close proximity to": "near", "in the vicinity of": "near",
    "is indicative of": "shows", "are indicative of": "show",
    "has the capability to": "can", "have the ability to": "can",
    "is reflective of": "reflects", "serves to": "",
    "it is important to note that": "", "it should be noted that": "",
    "it is worth noting that": "", "needless to say": "",
}

#: Verb inflections, so the list does not have to spell out every tense.
#: "utilise" is in the table and "utilised" is not, and `\b` stops the former
#: matching the latter — so the commonest written form was sailing through a
#: check that looked like it covered it.
_INFLECTIONS = ("", "d", "s", "ed", "es", "ing")


def _forms(word: str) -> list[str]:
    if " " in word:                     # phrases do not inflect
        return [word]
    stem = word[:-1] if word.endswith("e") else word
    return sorted({word, *(stem + suffix for suffix in _INFLECTIONS if suffix)},
                  key=len, reverse=True)


_PLAIN_FORMS: dict[str, str] = {}
for _word, _plain in PLAIN_WORDS.items():
    for _form in _forms(_word):
        _PLAIN_FORMS.setdefault(_form.lower(), _plain)

_PLAIN = re.compile(
    r"\b(" + "|".join(sorted((re.escape(w) for w in _PLAIN_FORMS), key=len, reverse=True)) + r")\b",
    re.IGNORECASE)


# Accusation without attribution.
#
# The difference the client drew, in their words: you can say "Steve is a
# thief", or you can say "the evidence strongly suggests Steve was involved
# in...". The first is a statement of fact the file cannot support and the
# publisher owns; the second is a finding attributed to evidence.
#
# This is not hedging, and the distinction matters because the house style
# above forbids hedging. "Perhaps Steve is a thief" is weaker AND still
# accuses. Attribution is the opposite move: it states the finding plainly
# and says what it rests on. The Economist's own practice is the model —
# allegations are reported as allegations, with their source, in a plain
# declarative sentence.
_ACCUSATION = re.compile(
    r"\b(thief|thieves|crook|fraudster|criminal|corrupt|corruptly|"
    r"stole|stolen|embezzl\w+|looted|bribed|bribery|kickback\w*|"
    r"laundered|rigged|fixed the tender)\b", re.IGNORECASE)

#: What turns an accusation into a reported one: a REPORTING construction,
#: not merely the presence of a legal-sounding noun.
#:
#: The first version listed "audit", "court", "evidence" and "investigation"
#: among these, so "Prior to the audit, Steve is a thief" counted as
#: attributed — the noun appeared, incidentally, in a sentence that accuses
#: flatly. Only verbs of reporting and finding can carry attribution, because
#: only they say somebody did the alleging.
#:
#: Biased toward flagging, deliberately. A properly attributed sentence that
#: gets flagged costs an editor five seconds. An unattributed allegation that
#: does not get flagged is published, and this file is sold to clients about
#: named living people.
_ATTRIBUTION = re.compile(
    r"\b(alleg\w+|accus\w+|claim\w+|report\w+|according to|said|says|stated|"
    r"charged with|convicted|acquitted|indicted|denies|denied|"
    r"testimony|testified|witness\w*|ruled|found|concluded|established|determined|"
    r"under investigation|faces charges|was named in)\b", re.IGNORECASE)

# Sentence splitting lives in `prose.py`. The pattern that used to be here,
# `[^.!?]+[.!?]|[^.!?]+$`, split inside numbers: "Sh4.8 trillion" became two
# sentences, one of them four characters long. That is not cosmetic for this
# file, because the check below looks for an accusation and its attribution
# WITHIN ONE SENTENCE — a split through the middle of a figure can leave the
# allegation in one fragment and the "police said" that attributes it in the
# next, and the check then passes a sentence it should have flagged.


def accusations(text: str) -> list[dict]:
    """Sentences that accuse without saying who is accusing.

    A due-diligence file may report any allegation. It may not make one.
    """
    hits: list[dict] = []
    for sentence in prose.sentences(text):
        found = _ACCUSATION.search(sentence)
        if found and not _ATTRIBUTION.search(sentence):
            hits.append({"kind": "unattributed_allegation",
                         "phrase": found.group(0),
                         "context": sentence[:220],
                         "fix": "attribute it — who alleges this, and on what record?"})
    return hits


def check(text: str) -> list[dict]:
    """Every house-style violation found, with the offending phrase and where."""
    if not text:
        return []
    hits: list[dict] = []
    for pattern, kind in ((_FIRST_PERSON, "first_person"), (_HEDGES, "hedge")):
        for match in pattern.finditer(text):
            hits.append({"kind": kind, "phrase": match.group(0),
                        "context": text[max(0, match.start() - 30):match.end() + 30].strip()})
    for match in _PLAIN.finditer(text):
        word = match.group(0)
        plain = _PLAIN_FORMS.get(word.lower(), "")
        hits.append({"kind": "inflated_word", "phrase": word,
                     "fix": f"use \u201c{plain}\u201d" if plain else "cut it",
                     "context": text[max(0, match.start() - 30):match.end() + 30].strip()})
    hits.extend(accusations(text))
    return hits


#: Keys whose values are not prose anybody wrote.
_SKIP_KEYS = {
    "ref", "refs", "id", "url", "source_url", "link", "href", "platform",
    "handle", "author", "username", "date", "posted_at", "published_at",
    "kind", "type", "stance", "confidence", "outlets",
}


def _walk(node, path: str, flags: dict[str, list[dict]]) -> None:
    if isinstance(node, str):
        key = path.rsplit(".", 1)[-1]
        if key in _SKIP_KEYS or key.endswith("_citations"):
            return
        hits = check(node)
        if hits:
            flags[path] = hits
    elif isinstance(node, dict):
        for k, v in node.items():
            if k == "style_flags":
                continue
            _walk(v, f"{path}.{k}" if path else k, flags)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(item, f"{path}[{i}]", flags)


def annotate(analysis: dict, fields: tuple[str, ...] | None = None) -> dict:
    """Attach `style_flags` for every prose field that violates house style,
    without touching the prose itself. Returns a new dict; the input is not
    mutated.

    `fields` used to be a hand-written tuple defaulting to three issue-map
    keys, so this checked `involvement`, `tension_or_risk` and `verdict` and
    nothing else — not the executive brief, not the insights, not a single
    field of the Search report, which was never style-checked at all. That is
    the same mistake that let raw citations leak for three rounds: a list of
    field names falls behind the prompts that produce them, and it can only be
    corrected after somebody has already read the bad output.

    So walk the payload. Every string is prose unless its key says otherwise.
    `fields`, if given, still restricts the walk — some callers want one
    section — but the default is everything.
    """
    if not analysis:
        return analysis
    out = dict(analysis)
    flags: dict[str, list[dict]] = {}
    if fields:
        for field in fields:
            hits = check(out.get(field) or "")
            if hits:
                flags[field] = hits
    else:
        _walk(out, "", flags)
    if flags:
        out["style_flags"] = flags
    return out
