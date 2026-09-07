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

_REF_PATTERN = re.compile(r"\[ref[=:]\s*([\w-]+)\]", re.IGNORECASE)


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
    if not text or "[ref" not in text.lower():
        return text or "", []

    seen: dict[str, int] = {}
    citations: list[dict] = []

    def _replace(match: re.Match) -> str:
        ref = match.group(1)
        if ref not in seen:
            n = len(seen) + 1
            seen[ref] = n
            found = ref_index.get(ref) or {}
            citations.append({"n": n, "ref": ref, "url": found.get("url"),
                              "platform": found.get("platform")})
        return f"[{seen[ref]}]"

    rewritten = _REF_PATTERN.sub(_replace, text)
    return rewritten, citations


#: Prose fields on the issue-map analysis that carry inline [ref=...] markers
#: and have no dedicated quotes array of their own.
_PROSE_FIELDS = ("involvement", "tension_or_risk", "verdict")


def linkify_analysis(analysis: dict, mentions: list[dict]) -> dict:
    """Resolve inline refs across every prose field of an issue-map analysis,
    plus the free-text parts of narratives and sub-issues. Returns a new dict;
    the input is not mutated."""
    if not analysis:
        return analysis
    ref_index = build_ref_index(mentions)
    out = dict(analysis)

    for field in _PROSE_FIELDS:
        if out.get(field):
            text, citations = linkify(out[field], ref_index)
            out[field] = text
            if citations:
                out[f"{field}_citations"] = citations

    def _linkify_list(items, keys):
        result = []
        for item in items or []:
            if not isinstance(item, dict):
                result.append(item)
                continue
            item = dict(item)
            for key in keys:
                if item.get(key):
                    text, citations = linkify(item[key], ref_index)
                    item[key] = text
                    if citations:
                        item[f"{key}_citations"] = citations
            result.append(item)
        return result

    if out.get("linking_narratives"):
        out["linking_narratives"] = _linkify_list(out["linking_narratives"],
                                                   ("summary", "detail"))
    if out.get("sub_issues"):
        out["sub_issues"] = _linkify_list(out["sub_issues"], ("detail",))

    return out
