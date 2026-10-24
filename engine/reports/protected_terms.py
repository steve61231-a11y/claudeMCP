"""Identity framing the sources never contained.

A live report on a Kenyan politician came back referring to Jews. Nothing in
the corpus did. The model had reached for a conspiracy frame that exists in
its training data and not in the evidence, and written it into a document
intended for a paying client.

That is the worst thing this engine can do. Every other failure in this
repository costs time; this one is defamatory, it is the oldest libel in
Europe arriving uninvited in a report about Kenyan politics, and one
occurrence would end the product. In a market whose politics are organised
around ethnicity it is not hypothetical: the same reflex that supplies
"Jewish financiers" supplies "the Kikuyu establishment" for a story that
never mentioned a community at all.

## Why this is a grounding problem and not a word problem

The obvious response is a list of forbidden words. It is the wrong response,
and it would break the product: Kenyan political reporting legitimately
concerns ethnic coalitions, Muslim communities in Mombasa and the north, and
religious leaders who are quoted by name. A report that cannot say "Luo"
cannot describe Kenyan politics, and one that silently drops a community a
source named is lying by omission.

So the rule is the one `grounding.py` already uses, pointed at identity:

    **If no source in the corpus contains the term, the analyst may not
    introduce it.**

Grounded use passes untouched — the sources raised it, the report may
discuss it. Ungrounded use is the model's invention, and there is no reading
of an invented ethnic or religious attribution that is safe to print.

## Why this one rewrites, when grounding.py does not

`grounding.py` reports and never edits, because a silently deleted sentence
is indistinguishable from a section with nothing to say — this codebase's
founding rule. That reasoning holds for an ungrounded NAME, which is
embarrassing. It does not hold here. A sentence attributing a Kenyan
political outcome to Jews is not improved by shipping it with a footnote.

The founding rule is honoured by the other half: every removal is recorded,
counted, and surfaced in the stage ledger with the sentence that was cut and
the term that triggered it. The gap is declared, loudly, which is the
opposite of a silent deletion.
"""

from __future__ import annotations

import re

from engine.reports import prose
from engine.reports.grounding import corpus_haystack

#: Families of protected characteristics, as word stems.
#:
#: Ethnicity is listed for Kenya specifically, because that is this product's
#: market and the generic lists do not contain the words that matter here. A
#: term earns its place by being one a model might reach for to EXPLAIN a
#: political event — which is precisely the move that has to be grounded.
#:
#: These are not banned words. Every one of them is printable when a source
#: used it. The list only decides what has to be checked.
PROTECTED = {
    "religion": (
        "jew", "jewish", "jews", "zionist", "zionism", "semitic", "semitism",
        "muslim", "islamic", "islamist", "christian", "catholic", "protestant",
        "evangelical", "hindu", "sikh", "buddhist", "atheist", "infidel",
    ),
    "ethnicity": (
        # Kenya's larger communities, and the ones political coverage names.
        "kikuyu", "gikuyu", "luo", "luhya", "kalenjin", "kamba", "kisii",
        "meru", "mijikenda", "maasai", "turkana", "somali", "borana",
        "embu", "taita", "pokot", "samburu", "nubian", "swahili people",
        # And the vocabulary of ethnic explanation itself.
        "tribal", "tribalism", "tribe", "ethnic bloc", "clannish",
    ),
    "race": (
        "black", "white", "asian", "arab", "african american", "caucasian",
        "indigenous", "racial", "racist", "racism",
    ),
    "nationality": (
        "chinese", "indian", "pakistani", "nigerian", "ugandan", "tanzanian",
        "rwandan", "ethiopian", "eritrean", "sudanese", "israeli",
        "palestinian", "iranian", "russian", "american",
    ),
    "sexuality_and_sex": (
        "gay", "lesbian", "homosexual", "lgbt", "lgbtq", "queer",
        "transgender", "feminist",
    ),
    "other": (
        "caste", "immigrant", "migrant", "refugee", "disabled", "mentally ill",
    ),
}

#: Flattened once, longest first, so "african american" is tested before
#: "american" and the family reported is the specific one.
_ALL: tuple[tuple[str, str], ...] = tuple(sorted(
    ((term, family) for family, terms in PROTECTED.items() for term in terms),
    key=lambda pair: -len(pair[0]),
))


def _mentions_term(text: str, term: str) -> bool:
    """Whole words only.

    Substring matching would fire "jew" on "jewel" and "black" on
    "blackout", and a check that flags correct prose is one its reader
    learns to skip — the conclusion `style_check.py` reached the hard way.
    """
    return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None


def _grounded_terms(haystack: str) -> set[str]:
    """Which protected terms the SOURCES themselves used.

    These are then printable. The sources raised the subject; a report that
    silently dropped a community its own evidence named would be lying by
    omission, which is the failure this file is built to avoid in the other
    direction.
    """
    return {term for term, _family in _ALL if _mentions_term(haystack, term)}


def introduced(text: str, source: str) -> tuple[str, str] | None:
    """The first protected term `text` uses that `source` never did, with
    its family — or None.

    The same rule as `scan`, held to ONE source rather than the corpus: the
    fact store's statements are a model's paraphrase of a verified quote,
    and a paraphrase that names a community its own source never named is
    the model's addition however exact the quote beneath it is."""
    allowed = _grounded_terms((source or "").lower())
    lowered = (text or "").lower()
    for term, family in _ALL:
        if term not in allowed and _mentions_term(lowered, term):
            return term, family
    return None


def scan(payload: dict, mentions: list[dict]) -> dict:
    """Sentences introducing identity the corpus never mentioned.

    Reports; `redact` is what removes. Returns a disclosure rather than a
    verdict, and an empty result is a real finding — "we looked and found
    none" and "nobody looked" must never render the same.
    """
    haystack = corpus_haystack(mentions)
    if not haystack:
        # With no corpus, everything would look ungrounded and the whole
        # report would be cut. Refuse to judge rather than judge wrongly.
        return {"checked": False, "findings": [],
                "skipped": "no corpus text to check against"}

    allowed = _grounded_terms(haystack)
    findings: list[dict] = []
    for field, text in _prose_fields(payload):
        for sentence in prose.sentences(text):
            lowered = sentence.lower()
            for term, family in _ALL:
                if term in allowed or not _mentions_term(lowered, term):
                    continue
                findings.append({
                    "field": field,
                    "term": term,
                    "family": family,
                    "sentence": sentence,
                })
                break   # One finding per sentence; the sentence goes either way.
    return {"checked": True, "findings": findings,
            "grounded_terms": sorted(allowed)}


def redact(payload: dict, mentions: list[dict]) -> dict:
    """Remove those sentences, and say exactly what was removed.

    The rewrite this codebase otherwise refuses — see the module docstring
    for why identity is the exception, and why the disclosure is what keeps
    the founding rule intact.
    """
    report = scan(payload, mentions)
    if not report.get("findings"):
        return report

    cut = {(f["field"], f["sentence"]) for f in report["findings"]}
    _rewrite(payload, cut)
    report["removed"] = len(cut)
    report["note"] = (
        f"{len(cut)} sentence(s) were removed before this report was shown. "
        "Each introduced an ethnic, religious, racial or other protected "
        "characteristic that appears nowhere in the sources collected for "
        "this subject. The analysis did not get that from the evidence, so "
        "it is not evidence — it is removed rather than flagged because "
        "there is no safe way to print an invented attribution of that kind. "
        "The sentences are listed here in full so the removal can be checked."
    )
    return report


def _prose_fields(payload, prefix: str = "") -> list[tuple[str, str]]:
    """Every prose string in the report, with the path that reached it.

    Walks rather than naming fields. `citations.py` records what naming
    fields one at a time costs: a new analyst is added, nobody updates the
    list, and the one page that needed checking is the page that is not.
    """
    out: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            out.extend(_prose_fields(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            out.extend(_prose_fields(value, f"{prefix}[{index}]"))
    elif isinstance(payload, str) and len(payload) > 40:
        # Short strings are labels, ids and enum values, not analysis.
        out.append((prefix, payload))
    return out


def _rewrite(payload, cut: set[tuple[str, str]], prefix: str = ""):
    """Drop the named sentences in place, keeping the rest of the prose."""
    if isinstance(payload, dict):
        for key, value in list(payload.items()):
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, str):
                payload[key] = _without(path, value, cut)
            else:
                _rewrite(value, cut, path)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            path = f"{prefix}[{index}]"
            if isinstance(value, str):
                payload[index] = _without(path, value, cut)
            else:
                _rewrite(value, cut, path)


def _without(path: str, text: str, cut: set[tuple[str, str]]) -> str:
    kept = [s for s in prose.sentences(text) if (path, s) not in cut]
    if len(kept) == len(prose.sentences(text)):
        return text
    return " ".join(kept).strip()
