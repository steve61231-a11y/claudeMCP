"""How good is this report — measured, not believed.

Phase 0 of `docs/FOUNDATION.md`. The founder's standard is "no room for
hallucination" and data that is "legit": if a report says a person did
something, a source has to say so. Without a measurement "better" is an
opinion, and until now every change was judged by eye on one report. This
computes, for every report, from the report and the corpus it was written
from, with no model involved:

  * **Depth of the corpus** — how many news items were read as articles
    rather than headlines (the largest source gave headlines only until
    22 Oct 2026), how many are the state's own records, how much of it is
    reaction rather than publication, how many outlets.
  * **Support for the prose** — of the sentences the analysts wrote, how
    many carry a source inline, how many sit in an item backed by verified
    quotes, and how many have neither; how many citations point at nothing
    in the corpus; how many figures match no number the engine computed and
    none in the sources.
  * **Completeness** — which stages failed, came out empty, or were skipped.

It reports; it does not edit. The guards that remove sentences
(`grounding.redact`, `protected_terms.redact`) run before it, and their
removals are counted here so a cleaner-looking report is not mistaken for a
more truthful one.

What it cannot do is say a supported sentence is TRUE — a sentence can cite
a real article and misstate it. That is the checker of phase 3. These are
floors: a figure that matches nothing is certainly worth a look, and a
figure that matches something is only not obviously invented.
"""

from __future__ import annotations

import re
from datetime import datetime

from engine.build import running_commit
from engine.reports import outlets
from engine.reports.citations import _CITATIONS_SUFFIX, _NEVER_PROSE
from engine.reports.prose import sentences

VERSION = 1

#: Not analyst prose: the engine's arithmetic, the sources' own words, and
#: the run's account of itself. Walking them would count the sources'
#: sentences as the analysts' and the engine's sentences as claims.
_NOT_ANALYST_PROSE = frozenset({
    # the sources' words
    "quotes", "evidence", "narrative_evidence", "representative", "items",
    "top_mentions", "notable_mentions", "sources_detail", "receipts",
    # the engine's arithmetic, and pages built from it
    "volume_trends", "sentiment_breakdown", "influence_summary", "period_series",
    "sentiment_framework", "network_insights", "coverage", "data_coverage",
    "sentiment_scoring", "since_last_report", "sentiment_history",
    "sentiment_history_note", "narrative_corpus", "subject_profile", "actions",
    # the run's account of itself
    "section_status", "run_health", "grounding", "protected_terms", "style_flags",
    "quality", "back_office", "data_provenance", "evidence_gate", "grade",
    "nothing_collected", "derived_sections", "ref_index", "corpus_preview",
    "freshness", "origin", "brief_check", "established_record", "fact_store",
})
#: Where the numbers the ENGINE computed live. A figure in the prose that
#: matches one of these was not invented. Numbers in the analysts' own
#: structured fields do not count: the same model wrote them.
_COMPUTED = frozenset({
    "volume_trends", "sentiment_breakdown", "influence_summary", "narrative_breakdown",
    "sentiment_framework", "period_series", "coverage", "data_coverage",
    "sentiment_scoring", "since_last_report", "sentiment_history", "narrative_corpus",
    "network_insights", "data_provenance",
})
#: Keys that hold labels rather than prose, beyond the ones citations.py
#: already refuses to treat as prose.
_LABEL_KEYS = frozenset({"label", "status", "name", "subject_type", "when", "outlets"})
#: An item carrying any of these is backed by the sources it names.
_BACKING_KEYS = ("quotes", "ref", "refs", "mention_ids")

#: Shorter strings are labels, ids and enum values — the threshold
#: `protected_terms` uses for the same question.
_MIN_PROSE = 40

_CITED = re.compile(r"\[\d+\]")
_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
          r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
#: A day of the month is a date, not a figure: "15 October", "October 15".
_DATE = re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b|\b{_MONTH}\s+\d{{1,2}}\b")
_FIGURE = re.compile(
    r"(?<![\w.:/-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(\s?%|\s?per\s?cent|\s?percent)?(?![\w:/-])", re.IGNORECASE)
_YEARS = range(1900, 2101)
#: Every number a source states, however it is attached: a source's
#: "56-year-old", "2022-2027" or "KSh3.2bn" supports an analyst's "aged 56".
#: The prose side stays strict (`_FIGURE`) so ids and dates are not judged.
_ANY_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# The analysts' prose
# ---------------------------------------------------------------------------

def _is_quote(node: dict) -> bool:
    return "text" in node and "ref" in node


def _backed(node: dict) -> bool:
    return any(node.get(key) for key in _BACKING_KEYS)


def analyst_prose(payload: dict, refs: set[str]) -> list[tuple[str, bool, frozenset]]:
    """Every string the analysts wrote: (text, whether its item is backed by
    sources, the citation numbers in it that point at no collected item).

    Walks rather than naming fields, like `citations` and `protected_terms`:
    a list of fields falls behind the day an analyst gains one. A citation
    to a ref the corpus does not hold is the model inventing a source, so it
    must not count as one."""
    out: list[tuple[str, bool, frozenset]] = []
    skip = _NOT_ANALYST_PROSE | set(payload.get("derived_sections") or [])

    def walk(node, key, backed, dead):
        if key in skip or (key or "").endswith(_CITATIONS_SUFFIX):
            return
        if isinstance(node, str):
            if (key not in _NEVER_PROSE and key not in _LABEL_KEYS
                    and len(node) > _MIN_PROSE and " " in node
                    and not node.lstrip().lower().startswith(("http", "www."))):
                out.append((node, backed, dead))
        elif isinstance(node, dict):
            if _is_quote(node):
                return
            here = backed or _backed(node)
            for k, v in node.items():
                cited = node.get(f"{k}{_CITATIONS_SUFFIX}") or []
                gone = frozenset(c.get("n") for c in cited
                                 if isinstance(c, dict) and c.get("ref") not in refs)
                walk(v, k, here, gone)
        elif isinstance(node, list):
            for item in node:
                walk(item, key, backed, dead)

    walk({k: v for k, v in (payload or {}).items()}, None, False, frozenset())
    return out


def _sourced(sentence: str, dead: frozenset) -> bool:
    return any(int(n) not in dead for n in re.findall(r"\[(\d+)\]", sentence))


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

#: A key that holds a percentage or a fraction: `negative_pct`, `share_pct`,
#: `shares`, `growth_rate`, `promo_ratio`. Matched on whole words, so
#: `generated_at` and `shared_terms` are not.
_SHARE_KEY = re.compile(r"(?:^|_)(?:pct|percent|share|shares|ratio|rate)(?:$|_)")


def _numbers(node, counts: set, shares: set, as_share: bool = False) -> None:
    """Sort the payload's numbers into counts and percentages by the key that
    holds them. Kept apart because "37%" matching a day's count of 37 is a
    coincidence, not a source."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        value = round(float(node), 1)
        if as_share:
            shares.add(value)
            if 0 < node <= 1:
                shares.add(round(float(node) * 100, 1))
        else:
            counts.add(value)
    elif isinstance(node, dict):
        for k, v in node.items():
            _numbers(v, counts, shares, as_share or bool(_SHARE_KEY.search(str(k))))
    elif isinstance(node, list):
        for v in node:
            _numbers(v, counts, shares, as_share)


def _corpus_counts(corpus: list[dict]) -> dict[str, dict[str, int]]:
    by: dict[str, dict[str, int]] = {"platform": {}, "source_type": {}, "segment": {}}
    for m in corpus:
        for field, value in (("platform", m.get("platform")),
                             ("source_type", m.get("source_type")),
                             ("segment", outlets.classify(m)[0])):
            by[field][value or "?"] = by[field].get(value or "?", 0) + 1
    return by


def computed_figures(payload: dict, corpus: list[dict]) -> tuple[set, set]:
    """(counts, percentages) the engine computed for this report."""
    counts: set[float] = set()
    shares: set[float] = set()
    for key in _COMPUTED | set(payload.get("derived_sections") or []):
        _numbers(payload.get(key), counts, shares)
    total = len(corpus)
    counts.add(float(total))
    for group in _corpus_counts(corpus).values():
        for n in group.values():
            counts.add(float(n))
            if total:
                shares.add(round(100 * n / total, 1))
    return counts, shares


def _normal(token: str) -> str:
    return token.replace(",", "")


def corpus_figures(corpus: list[dict]) -> set[str]:
    found: set[str] = set()
    for m in corpus:
        found.update(_normal(n) for n in _ANY_NUMBER.findall(m.get("text") or ""))
    return found


def figures_in(sentence: str) -> list[tuple[str, float, bool]]:
    """(as written, value, is a percentage) for each figure worth checking.

    Single digits are too often "three issues" or "2 points" to judge either
    way, years are dates, and a day of the month is part of a date."""
    text = _DATE.sub(" ", _CITED.sub(" ", sentence))
    found = []
    for match in _FIGURE.finditer(text):
        raw = match.group(1)
        try:
            value = float(_normal(raw))
        except ValueError:
            continue
        is_pct = bool(match.group(2))
        if not is_pct and (value < 10 or (value.is_integer() and int(value) in _YEARS)):
            continue
        found.append((match.group(0).strip(), value, is_pct))
    return found


def _figure_supported(token: str, value: float, is_pct: bool, counts: set,
                      shares: set, sourced: set) -> bool:
    if is_pct:
        if any(abs(value - k) <= 0.5 for k in shares):
            return True
    elif round(value, 1) in counts:
        return True
    return _normal(re.split(r"\s|%|per", token, maxsplit=1)[0]) in sourced


# ---------------------------------------------------------------------------
# The three measures
# ---------------------------------------------------------------------------

def corpus_depth(corpus: list[dict]) -> dict:
    items = len(corpus)
    articles = [m for m in corpus if m.get("source_type") == "article"]
    full = sum(1 for m in articles if m.get("full_text"))
    primary = sum(1 for m in corpus if m.get("source_type") in ("judgment", "gazette"))
    reactions = sum(1 for m in corpus if m.get("source_type") == "comment")
    dated = sum(1 for m in corpus if m.get("posted_at") and not m.get("date_estimated"))
    segments = _corpus_counts(corpus)["segment"]
    publishers = {outlets.publisher(m) for m in corpus if m.get("source_type") != "comment"}

    def pct(n, of):
        return round(100 * n / of, 1) if of else None

    return {
        "items": items,
        "articles": len(articles),
        "articles_full_text": full,
        "full_text_share": pct(full, len(articles)),
        "primary_records": primary,
        "reactions": reactions,
        "reaction_share": pct(reactions, items),
        "publishers": len(publishers),
        "segments": segments,
        "dated_share": pct(dated, items),
    }


def _citations(payload) -> list[dict]:
    found: list[dict] = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith(_CITATIONS_SUFFIX) and isinstance(v, list):
                    found.extend(c for c in v if isinstance(c, dict))
                else:
                    walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

    walk({k: v for k, v in (payload or {}).items() if k != "quality"})
    return found


def _count(node, key: str) -> int:
    """How many times `key` is set true anywhere under `node`."""
    if isinstance(node, dict):
        return int(bool(node.get(key))) + sum(_count(v, key) for v in node.values())
    if isinstance(node, list):
        return sum(_count(v, key) for v in node)
    return 0


def prose_support(payload: dict, corpus: list[dict]) -> dict:
    refs = {str(m.get("id", ""))[:8] for m in corpus}
    said = [(s, backed, _sourced(s, dead))
            for text, backed, dead in analyst_prose(payload, refs) for s in sentences(text)]
    cited = sum(1 for _, _, sourced in said if sourced)
    beside = sum(1 for _, backed, sourced in said if backed and not sourced)

    counts, shares = computed_figures(payload, corpus)
    sourced = corpus_figures(corpus)
    figures = unmatched = 0
    examples: list[dict] = []
    for sentence, _, _ in said:
        for token, value, is_pct in figures_in(sentence):
            figures += 1
            if not _figure_supported(token, value, is_pct, counts, shares, sourced):
                unmatched += 1
                if len(examples) < 8:
                    examples.append({"figure": token, "sentence": sentence[:240]})

    citations = _citations(payload)
    dangling = sorted({c.get("ref") for c in citations if c.get("ref") not in refs} - {None})
    grounding = payload.get("grounding") or {}
    identity = payload.get("protected_terms") or {}
    total = len(said)
    return {
        "sentences": total,
        "cited": cited,
        "beside_quotes": beside,
        "unsupported": total - cited - beside,
        "supported_share": round(100 * (cited + beside) / total, 1) if total else None,
        "cited_share": round(100 * cited / total, 1) if total else None,
        "citations": len(citations),
        "citations_to_nothing": len(dangling),
        "citations_to_nothing_refs": dangling[:10],
        "figures": figures,
        "figures_matching_nothing": unmatched,
        "figures_matching_nothing_examples": examples,
        "themes_without_a_quote": _count(payload, "quotes_unverified"),
        "names_not_in_sources": len(grounding.get("ungrounded") or []),
        "sentences_removed_for_names": int(grounding.get("removed") or 0),
        "sentences_removed_for_identity": int(identity.get("removed") or 0),
        "unverified_single_words": len(grounding.get("unverified_single_words") or []),
        "computed_sections": sorted(payload.get("derived_sections") or []),
    }


def completeness(payload: dict) -> dict:
    status = payload.get("section_status") or {}
    return {
        "failed": list(status.get("failed") or []),
        "empty": int(status.get("empty_count") or 0),
        "skipped": list(status.get("skipped") or []),
        "ok": int(status.get("ok_count") or 0),
    }


def headline(depth: dict, support: dict, sections: dict) -> str:
    parts = []
    if depth.get("full_text_share") is not None:
        parts.append(f"{depth['articles_full_text']} of {depth['articles']} news items "
                     f"read in full")
    if support.get("supported_share") is not None:
        parts.append(f"{support['cited'] + support['beside_quotes']} of "
                     f"{support['sentences']} analyst sentences backed by a source")
    if support.get("figures"):
        parts.append(f"{support['figures_matching_nothing']} of {support['figures']} "
                     "figures match nothing computed or sourced")
    if support.get("citations_to_nothing"):
        parts.append(f"{support['citations_to_nothing']} citation(s) point at no "
                     "collected item")
    if sections.get("failed"):
        parts.append(f"{len(sections['failed'])} stage(s) failed")
    return ("; ".join(parts) + ".") if parts else "Nothing to measure."


def assess(payload: dict, corpus: list[dict]) -> dict:
    """The report's quality, measured. Never raises: a measurement must not
    cost the report it measures — and a measurement that failed says so,
    rather than looking like a report with nothing wrong."""
    try:
        depth = corpus_depth(corpus or [])
        support = prose_support(payload or {}, corpus or [])
        sections = completeness(payload or {})
    except Exception as exc:  # noqa: BLE001
        return {"version": VERSION, "measured": False,
                "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {
        "version": VERSION,
        "measured": True,
        "measured_at": datetime.utcnow().isoformat(timespec="seconds"),
        # Which code wrote this report, so a change is judged by comparing
        # reports from before and after it rather than by eye on one.
        "build": running_commit(),
        "corpus": depth,
        "prose": support,
        "sections": sections,
        "headline": headline(depth, support, sections),
    }
