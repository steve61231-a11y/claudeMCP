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
    # Cheap reject before the regex, but it must not assume a bracket shape —
    # "(ref=" and a bare "ref=" both reach the page otherwise.
    if not text or "ref" not in text.lower():
        return text or "", []

    seen: dict[str, int] = {}
    citations: list[dict] = []

    def _replace(match: re.Match) -> str:
        # One marker can carry several ids — "(ref=fresh-0, fresh-1)" — and
        # each is a separate source that deserves its own number and link.
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

    rewritten = _REF_PATTERN.sub(_replace, text)
    return rewritten, citations


#: Prose fields on the issue-map analysis that carry inline [ref=...] markers
#: and have no dedicated quotes array of their own.
#: Every free-prose field an analyst writes refs into.
#:
#: This listed three fields and missed the rest, so a live map rendered a clean
#: "[1]" verdict at the top and then, four sections down, paragraphs reading
#: "operates globally alongside the World Bank [ref=2ddf5220]" — the model's
#: internal citation format, verbatim, in the body of a client deliverable.
#: `international` and `national` are written by the background analyst and
#: were never added here when that analyst was; the timeline's mini-briefings
#: are prose for the same reason and leaked the same way, three times over,
#: since the sequencing section reprints them.
_PROSE_FIELDS = ("involvement", "tension_or_risk", "verdict",
                 "international", "national")


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
    # The timeline's `event` is an 80-200 word mini-briefing, not a label, and
    # the sequencing section reprints it — so a raw ref here surfaced three
    # times in one report.
    if out.get("timeline"):
        out["timeline"] = _linkify_list(out["timeline"], ("event",))

    return out


#: The Search report's free-prose fields — the ones with no `quotes` array of
#: their own, which is exactly the condition that leaks raw refs.
#:
#: This was fixed for the issue map and not for the report, because the leak
#: was only ever SEEN on an issue map. Both run the same analysts under the
#: same GROUNDING_RULES, which instruct the model to "include that item's ref
#: id", so both were always going to do it.
_REPORT_PROSE_FIELDS = ("executive_brief", "executive_summary")


def linkify_report(payload: dict, mentions: list[dict]) -> dict:
    """Resolve inline refs across a Search report's prose. Returns a new dict;
    the input is not mutated."""
    if not payload:
        return payload
    ref_index = build_ref_index(mentions)
    out = dict(payload)

    for field in _REPORT_PROSE_FIELDS:
        if isinstance(out.get(field), str) and out[field]:
            text, cites = linkify(out[field], ref_index)
            out[field] = text
            if cites:
                out[f"{field}_citations"] = cites

    # "Beneath the surface": headline / reasoning / implication are prose, and
    # `the_one_thing` is the single line a decision-maker is meant to remember
    # — the worst possible place for "[ref=fresh-12]".
    insights = out.get("deep_insights")
    if isinstance(insights, dict):
        insights = dict(insights)
        if isinstance(insights.get("the_one_thing"), str) and insights["the_one_thing"]:
            text, cites = linkify(insights["the_one_thing"], ref_index)
            insights["the_one_thing"] = text
            if cites:
                insights["the_one_thing_citations"] = cites
        rows = []
        for item in insights.get("insights") or []:
            if not isinstance(item, dict):
                rows.append(item)
                continue
            item = dict(item)
            for key in ("headline", "reasoning", "implication"):
                if isinstance(item.get(key), str) and item[key]:
                    text, cites = linkify(item[key], ref_index)
                    item[key] = text
                    if cites:
                        item[f"{key}_citations"] = cites
            rows.append(item)
        insights["insights"] = rows
        out["deep_insights"] = insights

    dives = out.get("narrative_deep_dives")
    if isinstance(dives, list):
        out["narrative_deep_dives"] = [
            (dict(d, **dict(zip(("deep_dive", "deep_dive_citations"),
                                linkify(d["deep_dive"], ref_index))))
             if isinstance(d, dict) and isinstance(d.get("deep_dive"), str) and d["deep_dive"]
             else d)
            for d in dives
        ]

    return out
