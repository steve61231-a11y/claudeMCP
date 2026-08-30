"""Findings — evidence records grouped into claims a reader can act on.

A finding is not a paragraph. It is a structured object: a claim, the records
that support it, the records that contradict it, how many INDEPENDENT stories
those records come from, when it started, whether it is growing, and a
confidence that follows from those numbers rather than from a model's tone.

The rule the whole module exists to enforce: no evidence, no claim. A finding
cannot be constructed without records, every record carries a mention id, and
every mention id resolves to a stored item with a URL. If a sentence in the
final report cannot be traced to a row here, it does not belong in the report.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

from engine import stages
from engine import llm
from engine.evidence.independence import group_duplicates
from engine.evidence.records import (
    STATUS_ALLEGED,
    STATUS_OPINION,
    STATUS_REPORTED,
    EvidenceRecord,
)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


@dataclass
class Finding:
    """One issue or claim, with everything needed to check it."""

    title: str
    summary: str = ""
    kind: str = ""
    status: str = ""
    confidence: str = CONFIDENCE_LOW
    confidence_reason: str = ""
    mention_count: int = 0
    independent_sources: int = 0
    distinct_platforms: int = 0
    amplification: float = 1.0
    first_seen: str | None = None
    last_seen: str | None = None
    trend: str = "flat"
    trend_detail: str = ""
    sentiment: dict = field(default_factory=dict)
    supporting: list[dict] = field(default_factory=list)
    contradicting: list[dict] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    #: False when the contradiction search could not run for this finding.
    contradiction_checked: bool = True
    review: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _topic_key(record: EvidenceRecord) -> str:
    return (record.topic or record.statement[:40]).strip().lower()


def _evidence_row(record: EvidenceRecord) -> dict:
    return {
        "mention_id": record.mention_id,
        "statement": record.statement,
        "quote": record.quote,
        "kind": record.kind,
        "status": record.status,
        "actor": record.actor,
        "platform": record.platform,
        "author": record.author,
        "url": record.url,
        "posted_at": record.posted_at,
        "sentiment": record.sentiment,
    }


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _trend(records: list[EvidenceRecord]) -> tuple[str, str]:
    """Growing, fading or flat — stated with the numbers behind it.

    "People are talking about X" is not intelligence. "X went from 4 records in
    the first half of the window to 31 in the second" is."""
    dated = sorted((d for d in (_parse(r.posted_at) for r in records) if d))
    if len(dated) < 4:
        return "flat", "too few dated records to establish a trend"
    # Split the TIME SPAN in half, not the sorted list. Splitting at the median
    # record puts half the records on each side by construction, so every trend
    # came out "flat" — the measure could not detect the thing it was for.
    midpoint = dated[0] + (dated[-1] - dated[0]) / 2
    if dated[0] == dated[-1]:
        return "flat", "every dated record falls on the same day"
    first = sum(1 for d in dated if d < midpoint)
    second = len(dated) - first
    if first == 0:
        return "emerging", f"all {second} dated records fall in the later half of the window"
    ratio = second / first
    if ratio >= 1.5:
        return "growing", f"{first} records in the earlier half, {second} in the later"
    if ratio <= 0.67:
        return "fading", f"{first} records in the earlier half, {second} in the later"
    return "flat", f"{first} records in the earlier half, {second} in the later — no clear move"


def _confidence(independent: int, platforms: int, statuses: list[str],
                contradicting: int) -> tuple[str, str]:
    """Confidence from the evidence, not from the prose.

    Independence is weighted above volume throughout: forty copies of one story
    is one story, and a system that reads it as forty confirmations is the
    thing we are trying not to build.
    """
    reported = sum(1 for s in statuses if s == STATUS_REPORTED)
    opinion_only = all(s in (STATUS_OPINION, STATUS_ALLEGED) for s in statuses)

    if independent >= 4 and platforms >= 2 and reported >= 2 and not contradicting:
        return CONFIDENCE_HIGH, (
            f"{independent} independent stories across {platforms} platforms, "
            f"{reported} of them reported as fact, with nothing contradicting")
    if contradicting:
        return CONFIDENCE_LOW, (
            f"{contradicting} record(s) in the corpus contradict this; treat as unresolved")
    if opinion_only:
        return CONFIDENCE_LOW, (
            "every record is opinion or allegation — this is what people are saying, "
            "not something the corpus establishes")
    if independent >= 2:
        return CONFIDENCE_MEDIUM, (
            f"{independent} independent stories, but "
            + ("only one platform" if platforms < 2 else f"only {reported} reported as fact"))
    return CONFIDENCE_LOW, (
        f"only {independent} independent story behind this — a single origin, "
        "however many times it was repeated")


CONTRADICT_PROMPT = """You are testing a claim against a body of evidence, as a sceptical editor would.

CLAIM: {claim}

Numbered evidence from the corpus:
{evidence}

Identify which numbered items CONTRADICT the claim — state a fact or account
incompatible with it. Do NOT list items that merely fail to mention it, add
detail, or express a different opinion. Absence of support is not contradiction.

Also list genuinely open questions the evidence leaves unanswered (at most 3).

Respond with ONLY this JSON:
{{"contradicting": [<numbers>], "open_questions": ["..."]}}"""


def find_contradictions(claim: str, pool: list[EvidenceRecord],
                        limit: int = 24) -> tuple[list[int], list[str], bool]:
    """Actively look for evidence AGAINST a claim. Returns indices into `pool`.

    Runs over the WHOLE record set, including the claim's own cluster: topic
    clustering puts a claim and its refutation side by side, so the refutation
    would otherwise be filed as support for the thing it refutes."""
    if not claim or not pool:
        return [], [], False
    sample = pool[:limit]
    listing = "\n".join(f"[{i}] {r.statement}" for i, r in enumerate(sample, start=1))
    try:
        reply = llm.call_json(
            CONTRADICT_PROMPT.format(claim=claim[:300], evidence=listing),
            max_tokens=1200, model=llm.bulk_model())
    except Exception as exc:  # noqa: BLE001
        # No contradiction search ran. "Nothing contradicts this" and "we never
        # looked" must not read the same on a claim we are about to publish.
        stages.current().failed("contradiction_search", exc)
        return [], [], False
    indices = []
    for value in (reply.get("contradicting") or []):
        try:
            position = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= position <= len(sample):
            indices.append(position - 1)
    questions = [str(q).strip()[:200] for q in (reply.get("open_questions") or []) if str(q).strip()]
    return indices, questions[:3], True


SKEPTIC_PROMPT = """You are the sceptic. Your job is to try to DISPROVE a finding before it
reaches a client, not to improve its wording.

FINDING: {title}
Summary: {summary}
Stated confidence: {confidence} ({reason})
Raw mentions: {mentions}. Independent stories behind them: {independent}. Platforms: {platforms}.
Evidence statuses: {statuses}
Contradicting records found: {contradicting}

Supporting evidence:
{supporting}

Ask yourself:
  - Are these sources actually independent, or one story repeated?
  - Is opinion being reported as fact?
  - Does the evidence support the claim as worded, or something weaker?
  - Could this simply be a viral repost?
  - What evidence would make this conclusion wrong?

Return:
  "verdict"  — PASS (stands as written) | REVISE (true but overstated) | REJECT (not supported)
  "reason"   — one or two sentences, concrete
  "revised_title" — if REVISE, the claim reworded to what the evidence DOES support; else ""
  "what_would_disprove" — the specific evidence that would overturn it

Respond with ONLY this JSON:
{{"verdict": "...", "reason": "...", "revised_title": "...", "what_would_disprove": "..."}}"""


def challenge(finding: Finding) -> dict:
    """Adversarial review of one finding. A failure to review is recorded as
    'not reviewed' — never as a pass, because an unreviewed finding presented
    as reviewed is worse than an unreviewed one."""
    supporting = "\n".join(
        f"- [{row['status']}] {row['statement']} ({row.get('platform') or 'unknown'})"
        for row in finding.supporting[:12])
    try:
        reply = llm.call_json(
            SKEPTIC_PROMPT.format(
                title=finding.title, summary=finding.summary or "—",
                confidence=finding.confidence, reason=finding.confidence_reason,
                mentions=finding.mention_count, independent=finding.independent_sources,
                platforms=finding.distinct_platforms,
                statuses=", ".join(sorted({r["status"] for r in finding.supporting})) or "—",
                contradicting=len(finding.contradicting), supporting=supporting or "—"),
            max_tokens=900, model=llm.strong_model())
    except Exception:  # noqa: BLE001
        return {"verdict": "NOT_REVIEWED",
                "reason": "the sceptic pass did not complete for this finding"}
    verdict = str(reply.get("verdict") or "").strip().upper()
    if verdict not in ("PASS", "REVISE", "REJECT"):
        verdict = "NOT_REVIEWED"
    return {
        "verdict": verdict,
        "reason": str(reply.get("reason") or "").strip()[:400],
        "revised_title": str(reply.get("revised_title") or "").strip()[:200],
        "what_would_disprove": str(reply.get("what_would_disprove") or "").strip()[:300],
    }


def build_findings(records: list[EvidenceRecord], mentions: list[dict],
                   min_records: int = 2, top_n: int = 12,
                   review: bool = True) -> list[Finding]:
    """Group records into findings, measure them, and challenge the top ones."""
    if not records:
        return []

    by_mention = {m.get("id"): m for m in mentions}
    # Duplicate groups computed once over the whole corpus, so "independent
    # sources" means the same thing in every finding.
    origin_of: dict[str, str] = {}
    for group in group_duplicates(mentions):
        for mention_id in group.mention_ids:
            origin_of[mention_id] = group.key

    clusters: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for record in records:
        clusters[_topic_key(record)].append(record)

    findings: list[Finding] = []
    for key, group in clusters.items():
        if len(group) < min_records:
            continue
        mention_ids = [r.mention_id for r in group]
        stories = {origin_of.get(mid, mid) for mid in mention_ids}
        platforms = {r.platform for r in group if r.platform}
        dated = sorted(d for d in (_parse(r.posted_at) for r in group) if d)
        trend, trend_detail = _trend(group)

        tone: dict[str, int] = {}
        for record in group:
            if record.sentiment:
                tone[record.sentiment] = tone.get(record.sentiment, 0) + 1

        statuses = [r.status for r in group]
        confidence, reason = _confidence(len(stories), len(platforms), statuses, 0)

        headline = max(group, key=lambda r: len(r.statement))
        findings.append(Finding(
            title=(group[0].topic or headline.statement[:70]).strip(),
            summary=headline.statement,
            kind=max(set(r.kind for r in group), key=lambda k: statuses.count(k)) if group else "",
            status=max(set(statuses), key=statuses.count),
            confidence=confidence,
            confidence_reason=reason,
            mention_count=len(set(mention_ids)),
            independent_sources=len(stories),
            distinct_platforms=len(platforms),
            amplification=round(len(set(mention_ids)) / len(stories), 2) if stories else 1.0,
            first_seen=dated[0].isoformat() if dated else None,
            last_seen=dated[-1].isoformat() if dated else None,
            trend=trend,
            trend_detail=trend_detail,
            sentiment=tone,
            supporting=[_evidence_row(r) for r in group[:10]],
        ))

    # Rank by independent corroboration, NOT by volume. Ranking by mention
    # count would put the most-reposted item on top by definition.
    findings.sort(key=lambda f: (f.independent_sources, f.mention_count), reverse=True)
    findings = findings[:top_n]

    if review:
        for finding in findings:
            # The pool includes the finding's OWN cluster. Clustering is by
            # topic, so a claim and the report that refutes it land together —
            # "the stadium was abandoned" and "the contractor returned to site"
            # are the same topic. Excluding the cluster would file the refutation
            # as support for the thing it refutes, which is worse than missing
            # it. Anything flagged is moved out of supporting.
            indices, questions, searched = find_contradictions(
                finding.summary or finding.title, records)
            against = [records[i] for i in indices]
            finding.contradicting = [_evidence_row(r) for r in against]
            finding.open_questions = questions
            if against:
                contradicting_ids = {r.mention_id for r in against}
                finding.supporting = [row for row in finding.supporting
                                      if row["mention_id"] not in contradicting_ids]
            if finding.contradicting:
                # Contradiction changes the reading, so recompute rather than
                # leaving a confidence that was scored before we looked.
                finding.confidence, finding.confidence_reason = _confidence(
                    finding.independent_sources, finding.distinct_platforms,
                    [row["status"] for row in finding.supporting], len(finding.contradicting))
            elif not searched:
                # "Nothing contradicts this" is a claim about the corpus. If the
                # search never ran we have not earned it — HIGH confidence
                # reading "with nothing contradicting" would be an assertion
                # about evidence nobody looked at.
                finding.contradiction_checked = False
                if finding.confidence == CONFIDENCE_HIGH:
                    finding.confidence = CONFIDENCE_MEDIUM
                finding.confidence_reason = (
                    finding.confidence_reason.replace(", with nothing contradicting", "")
                    + " — the contradiction search did not run, so this is not a statement "
                      "that the corpus fails to contradict it")
            finding.review = challenge(finding)

    return findings


def unsupported(findings: list[Finding]) -> list[Finding]:
    """Findings the sceptic rejected. Kept, not deleted: what the system decided
    NOT to tell the client is part of the audit trail."""
    return [f for f in findings if (f.review or {}).get("verdict") == "REJECT"]
