"""Turn a model's inline `[ref=xxxx]` citations into real links.

The digest hands every analyst its source material pre-tagged with
`[ref=abcd1234 | platform ...]` headers (see `_render_mention`), because that
is how a quote gets validated back against its source. Free-prose fields
(verdict, involvement, tension_or_risk, narrative summaries, sub-issue
detail) have no separate `quotes` array to carry citations in, so the model
does the only thing it can: it imitates the ref-tag format inline, and the
raw `[ref=fresh-0]` string reached the page verbatim — meaningless to a
reader, and exactly the kind of "I believe" hedge-adjacent artifact that has
no place in a report going to a CEO.

This resolves those refs against the corpus once, replaces each with a small
sequential citation number scoped to that piece of text, and returns the
resolved URL for each — so the frontend can render `[1]` as a real link
instead of leaving `[ref=fresh-0]` sitting in the sentence.
"""

from __future__ import annotations

import re

#: Every shape a model writes an inline citation in.
#:
#: This was `\[ref[=:]\s*([\w-]+)\]` — one exact shape, square brackets, one
#: id. The models do not commit to that. A live map came back with
#: "(ref=fresh-0, fresh-1)": round brackets, and TWO ids in one marker. None
#: of it matched, so the raw text went straight to the page for the third
#: time, and each earlier fix had only taught the pattern one more spelling.
#:
#: So match the family rather than the instance: an optional bracket of either
#: kind, "ref"/"refs" with = or :, and a comma- or space-separated list of ids.
#: The `ref=` anchor is what makes this safe — ordinary prose does not contain
#: it, so widening the brackets cannot start eating real sentences.
_REF_PATTERN = re.compile(
    r"""
    (?:[\[\(]\s*)?          # opening bracket, if the model used one. The
                            # inner \s* is INSIDE this group on purpose: left
                            # outside, it swallowed the space before an
                            # unbracketed "ref=" and ran two words together.
    refs?\s*[=:]\s*         # ref= / refs: / ref :
    (                       # -- the ids --
      [\w-]+                 # first id
      (?:\s*,\s*[\w-]+)*    # ", second, third" in the same marker
    )
    (?:\s*[\]\)])?          # closing bracket, if there was one
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Splits the captured id list. A marker can carry several.
_REF_SPLIT = re.compile(r"\s*,\s*")


def build_ref_index(mentions: list[dict]) -> dict[str, dict]:
    """ref (8-char id prefix, matching `_render_mention`) -> where it came from."""
    index: dict[str, dict] = {}
    for mention in mentions or []:
        ref = str(mention.get("id", ""))[:8]
        if not ref or ref in index:
            continue
        url = (mention.get("raw_payload") or {}).get("url") or mention.get("source_url")
        index[ref] = {
            "url": url,
            "platform": mention.get("platform"),
            "posted_at": mention.get("posted_at"),
        }
    return index


def linkify(text: str, ref_index: dict[str, dict]) -> tuple[str, list[dict]]:
    """Replace every `[ref=xxxx]` with `[n]`, numbered in order of first
    appearance in THIS text. Returns (rewritten_text, citations) where
    citations is `[{"n": 1, "url": ..., "platform": ...}, ...]` — a ref this
    corpus can't resolve still gets a number (never silently dropped), just
    with `url: None`, so the frontend can render it as plain "[1]" rather
    than a link, honestly showing the source could not be traced.
    """
    seen: dict[str, int] = {}
    citations: list[dict] = []
    return _linkify_shared(text, ref_index, seen, citations), citations


# ---------------------------------------------------------------------------
# Coverage: every string is prose until proven otherwise.
#
# This used to be two hand-written lists of field names — `_PROSE_FIELDS` for
# the issue map, `_REPORT_PROSE_FIELDS` for the report. Both were written once
# against the fields whose leak had been SEEN, and both fell behind the moment
# an analyst gained a field. By the fourth round the lists were covering
# thirteen names and still missing six, among them `how_it_unfolded` — the
# 250-500 word deep-dive body, the longest prose block in the report — and
# `what_they_say`, whose entry in the list said `summary`, a key the analyst
# has never emitted. That one had been "fixed" and had never once run.
#
# A list of prose fields is the wrong shape for the problem. An analyst writes
# prose; which key it lands under is an implementation detail that changes
# whenever a prompt changes, and the list can only ever be updated *after* a
# reader has seen the raw ref. So invert it: walk the whole structure and
# treat every string as prose, except the handful of keys that structurally
# cannot be — ids, addresses, enum labels, dates — and anything that looks
# like a URL wherever it sits.
#
# This is safe to do broadly because `linkify` is anchored on a literal
# "ref=" / "ref:" and returns untouched text when it finds none: walking a
# field that holds no citation costs a substring check and changes nothing.
# ---------------------------------------------------------------------------

#: Keys whose values are never prose. Two reasons to be on this list:
#: mangling (a URL containing "?ref=twitter" would be rewritten into "[1]",
#: destroying the link), and noise (numbering a bare id or an enum).
_NEVER_PROSE = frozenset({
    "ref", "refs", "id", "mention_id", "document_id", "source_id",
    "url", "source_url", "link", "href", "permalink", "image", "avatar",
    "platform", "source", "source_type", "handle", "author", "username",
    "date", "posted_at", "published_at", "created_at", "updated_at",
    "stance", "confidence", "kind", "type", "lang", "language",
})

#: A URL anywhere — including under a key not on the list above, since models
#: put addresses in fields named `detail` and `evidence` as readily as in `url`.
_LOOKS_LIKE_URL = re.compile(r"^\s*(?:https?://|www\.)", re.IGNORECASE)

#: A whole URL, so a "ref=" inside a query string can be stepped over.
#:
#: `_LOOKS_LIKE_URL` only anchors at the start of a value, which catches a
#: field that IS an address but not an address quoted mid-sentence — and a
#: model citing "the filing at kenyalaw.org/view?ref=12345" had that URL
#: rewritten to ".../view?[1]", turning a working link into a dead one. The
#: substitution skips any match that begins inside one of these spans.
_URL_SPAN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

#: The suffix used for the citation list attached beside a linkified field.
#: Recognised on the way in so a second pass over an already-linkified payload
#: does not treat the citations it produced as prose to linkify again.
_CITATIONS_SUFFIX = "_citations"


def _is_prose(key: str | None, value: str) -> bool:
    if not value:
        return False
    if key is not None:
        if key in _NEVER_PROSE or key.endswith(_CITATIONS_SUFFIX):
            return False
    return not _LOOKS_LIKE_URL.match(value)


def _linkify_shared(text: str, ref_index: dict, seen: dict, citations: list) -> str:
    """`linkify`, but numbering continues across several calls.

    A list of strings under one key — `themes`, `who_is_driving_it` — shares a
    single citations array, so each string cannot restart at [1] or the page
    would show two different sources both numbered [1].
    """
    if not text or "ref" not in text.lower():
        return text or ""

    url_spans = [m.span() for m in _URL_SPAN.finditer(text)]

    def _replace(match: re.Match) -> str:
        at = match.start()
        if any(start <= at < end for start, end in url_spans):
            return match.group(0)    # a query parameter, not a citation
        numbers = []
        for ref in _REF_SPLIT.split(match.group(1)):
            ref = ref.strip()
            if not ref:
                continue
            if ref not in seen:
                n = len(seen) + 1
                seen[ref] = n
                found = ref_index.get(ref) or {}
                citations.append({"n": n, "ref": ref, "url": found.get("url"),
                                  "platform": found.get("platform")})
            numbers.append(f"[{seen[ref]}]")
        return "".join(numbers)

    return _REF_PATTERN.sub(_replace, text)


def _linkify_value(value, key, ref_index, seen, citations, used):
    """Resolve refs anywhere inside `value`. Citations for strings at THIS
    level accumulate into `citations`; nested dicts carry their own."""
    if isinstance(value, str):
        if key == "ref" and value:
            used.add(value)          # a quote's structured ref — keep, but note it
        if not _is_prose(key, value):
            return value
        return _linkify_shared(value, ref_index, seen, citations)
    if isinstance(value, list):
        return [_linkify_value(v, key, ref_index, seen, citations, used) for v in value]
    if isinstance(value, dict):
        return _linkify_mapping(value, ref_index, used)
    return value


def _linkify_mapping(node: dict, ref_index: dict, used: set) -> dict:
    out = {}
    for key, value in node.items():
        seen: dict[str, int] = {}
        citations: list[dict] = []
        out[key] = _linkify_value(value, key, ref_index, seen, citations, used)
        if citations:
            out[f"{key}{_CITATIONS_SUFFIX}"] = citations
            used.update(c["ref"] for c in citations)
    return out


def _resolve(payload: dict, mentions: list[dict]) -> dict:
    """Shared body of `linkify_analysis` and `linkify_report`.

    They differed only in which field names they knew about, which was the
    bug. They do the same thing, so they are the same function.
    """
    if not payload:
        return payload
    ref_index = build_ref_index(mentions)
    used: set[str] = set()
    out = _linkify_mapping(payload, ref_index, used)

    # Ship the resolved sources for the refs this payload actually uses, so
    # the page can render a quote's provenance — outlet and date — instead of
    # the raw 8-character id that `quotes[].ref` carries. Scoped to what is
    # cited rather than to the whole corpus: a run with 600 mentions and 40
    # quotes should not send 560 unused rows to the browser.
    #
    # Everything in here is stored in a JSONB column and served as JSON, so
    # it must be JSON-safe at the point it enters the payload. `posted_at`
    # arrives from the ORM as a `datetime`, which the rest of the index never
    # had to care about because it never left this module.
    resolved = {ref: {"url": ref_index[ref].get("url"),
                      "platform": ref_index[ref].get("platform"),
                      "posted_at": _as_text(ref_index[ref].get("posted_at"))}
                for ref in used if ref in ref_index}
    if resolved:
        out["ref_index"] = resolved
    return out


def _as_text(value):
    """A date the page can print, or None. Never a live object."""
    if value is None or isinstance(value, str):
        return value or None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def linkify_analysis(analysis: dict, mentions: list[dict]) -> dict:
    """Resolve inline refs across every prose field of an issue-map analysis.
    Returns a new dict; the input is not mutated."""
    return _resolve(analysis, mentions)


def linkify_report(payload: dict, mentions: list[dict]) -> dict:
    """Resolve inline refs across every prose field of a Search report.
    Returns a new dict; the input is not mutated."""
    return _resolve(payload, mentions)
