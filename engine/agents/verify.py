"""Verification agent — the runtime judge that stands between analysis and output.

Every earlier stage is generative: models read evidence and write prose. This
stage is adversarial. It takes what was written, breaks it into atomic factual
claims, goes back to the stored corpus for each one, and asks whether the
evidence actually says that.

Why this exists at all: a fluent, confident, wrong sentence is the single most
damaging thing an intelligence tool can produce. It is worse than silence,
because it gets acted on. So nothing reaches a report as fact unless a specific
stored passage supports it, and anything unsupported is labelled rather than
quietly deleted — a claim the evidence can't back is itself a finding (it marks
where the file is thin and what to investigate next).

Confidence is not the model's self-assessment. It is derived from how many
INDEPENDENT sources corroborate the claim, which is the thing that actually
distinguishes a fact from a rumour that got repeated.
"""

from concurrent.futures import ThreadPoolExecutor

from engine import llm
from engine.agents import evidence as evidence_store
from engine.db.models import Claim, ClaimEvidence

# Sections whose prose asserts things about the world and therefore needs
# checking. Descriptive/statistical sections are computed from the data itself.
VERIFIABLE_SECTIONS = ("executive_brief", "summary", "risks", "opportunities", "trends", "insights")

VERIFIED = "verified"
UNVERIFIED = "unverified"
CONTRADICTED = "contradicted"

_MAX_CLAIMS_PER_SECTION = 12
_WORKERS = 4


EXTRACT_PROMPT = """You are auditing an intelligence report before it is released.

Break the passage below into ATOMIC factual claims — each a single, self-contained,
checkable assertion about the subject or the world.

Rules:
- Split compound sentences into separate claims.
- Keep names, numbers, dates and organisations in each claim so it stands alone.
- Include only assertions of FACT. Skip recommendations, questions, hedged
  speculation about the future, and pure opinion.
- Copy the substance faithfully; do not add, soften or embellish.

Passage:
{passage}

Respond with ONLY this JSON:
{{"claims": ["claim one", "claim two"]}}"""


ADJUDICATE_PROMPT = """You are verifying one claim from an intelligence report against the ONLY evidence available.

CLAIM:
{claim}

EVIDENCE (numbered passages retrieved from the stored corpus):
{evidence}

Decide, using the evidence and nothing else — not your own background knowledge:
  - "verified"     : the evidence directly supports the claim.
  - "contradicted" : the evidence directly contradicts it.
  - "unverified"   : the evidence neither supports nor contradicts it. Partial,
                     tangential or merely topically-related evidence is
                     UNVERIFIED, not verified.

Then list the numbers of the evidence passages that actually bear on the claim.

Be strict. A claim that sounds plausible but is not shown by these passages is
unverified. Saying "unverified" is always better than asserting something the
evidence does not establish.

Respond with ONLY this JSON:
{{"verdict": "verified|contradicted|unverified", "support": [1, 2], "reason": "one sentence"}}"""


def extract_claims(passage: str, max_claims: int = _MAX_CLAIMS_PER_SECTION) -> list[str]:
    """Decompose written prose into atomic checkable assertions."""
    if not passage or not passage.strip():
        return []
    try:
        result = llm.call_json(
            EXTRACT_PROMPT.format(passage=passage[:6000]),
            max_tokens=1500,
            model=llm.bulk_model(),
        )
    except Exception:  # noqa: BLE001
        return []
    claims = [str(c).strip() for c in (result.get("claims") or []) if str(c).strip()]
    return claims[:max_claims]


def adjudicate(claim: str, evidence: list[dict]) -> dict:
    """Judge one claim against retrieved evidence.

    With no evidence at all there is nothing to judge, so the claim is
    unverified by definition — and we don't spend a call to learn that.
    """
    if not evidence:
        return {"verdict": UNVERIFIED, "support": [], "reason": "no supporting evidence found in the corpus"}

    rendered = "\n".join(
        f"[{i}] ({e.get('source') or 'unknown source'}) {e.get('passage', '')[:400]}"
        for i, e in enumerate(evidence, start=1)
    )
    try:
        result = llm.call_json_untrusted(
            ADJUDICATE_PROMPT.format(claim=claim, evidence=rendered),
            rendered,
            expected_keys={"verdict"},
            max_tokens=400,
            max_untrusted_chars=len(rendered) + 500,
        )
    except Exception:  # noqa: BLE001
        # If the judge itself fails we must not upgrade the claim's status.
        return {"verdict": UNVERIFIED, "support": [], "reason": "verification unavailable"}

    verdict = str(result.get("verdict") or UNVERIFIED).lower()
    if verdict not in (VERIFIED, CONTRADICTED, UNVERIFIED):
        verdict = UNVERIFIED
    support = []
    for index in result.get("support") or []:
        try:
            position = int(index)
        except (TypeError, ValueError):
            continue
        if 1 <= position <= len(evidence):
            support.append(position)
    return {"verdict": verdict, "support": support, "reason": str(result.get("reason") or "")[:300]}


def _confidence(verdict: str, independent_sources: int) -> float:
    """Confidence from corroboration, not from the model's self-belief."""
    if verdict == CONTRADICTED:
        return 0.1
    if verdict != VERIFIED:
        return 0.3
    return {0: 0.5, 1: 0.6, 2: 0.75}.get(independent_sources, 0.9)


def _judge(section: str, claim_text: str, found: list[dict]) -> dict:
    """Adjudicate one already-retrieved claim. Pure LLM work, so it is safe to
    run concurrently — database access happens on the caller's session."""
    outcome = adjudicate(claim_text, found)
    supporting = [found[i - 1] for i in outcome["support"]] or (
        found[:2] if outcome["verdict"] == VERIFIED else []
    )
    independent = evidence_store.independent_source_count(supporting)
    return {
        "section": section,
        "text": claim_text,
        "verdict": outcome["verdict"],
        "reason": outcome["reason"],
        "evidence": supporting,
        "independent_sources": independent,
        "confidence": _confidence(outcome["verdict"], independent),
    }


def verify_payload(db, politician, payload: dict, report_id: str | None = None) -> dict:
    """Audit a finished report payload and persist the verdicts.

    Returns a summary plus the per-claim results. Callers decide presentation;
    this stage's contract is that every claim ends up with a recorded status and
    its citations, so nothing is asserted on the model's word alone.
    """
    targets: list[tuple[str, str]] = []
    for section in VERIFIABLE_SECTIONS:
        value = payload.get(section)
        if isinstance(value, str):
            targets.append((section, value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    targets.append((section, item))

    claims: list[tuple[str, str]] = []
    for section, passage in targets:
        for claim_text in extract_claims(passage):
            claims.append((section, claim_text))

    if not claims:
        return {"checked": 0, "verified": 0, "unverified": 0, "contradicted": 0, "claims": []}

    # Retrieval is fast SQL on the caller's session (sessions are not
    # thread-safe); only the slow LLM adjudication is parallelised.
    retrieved = [
        (section, text, evidence_store.retrieve_for_claim(db, politician.id, text))
        for section, text in claims
    ]
    workers = min(_WORKERS, len(retrieved))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda c: _judge(c[0], c[1], c[2]), retrieved))

    counts = {VERIFIED: 0, UNVERIFIED: 0, CONTRADICTED: 0}
    for result in results:
        counts[result["verdict"]] = counts.get(result["verdict"], 0) + 1
        claim_row = Claim(
            politician_id=politician.id,
            report_id=report_id,
            text=result["text"][:4000],
            section=result["section"],
            claim_type="fact",
            status=result["verdict"],
            confidence=result["confidence"],
            evidence_count=len(result["evidence"]),
            independent_sources=result["independent_sources"],
            verifier_note=result["reason"],
        )
        db.add(claim_row)
        db.flush()
        for item in result["evidence"]:
            db.add(
                ClaimEvidence(
                    claim_id=claim_row.id,
                    mention_id=item.get("mention_id"),
                    document_id=item.get("document_id"),
                    quote=(item.get("passage") or "")[:2000],
                    url=item.get("url"),
                    stance="supports" if result["verdict"] == VERIFIED else "contradicts"
                    if result["verdict"] == CONTRADICTED
                    else None,
                )
            )
    db.commit()

    return {
        "checked": len(results),
        "verified": counts.get(VERIFIED, 0),
        "unverified": counts.get(UNVERIFIED, 0),
        "contradicted": counts.get(CONTRADICTED, 0),
        "claims": [
            {
                "section": r["section"],
                "text": r["text"],
                "status": r["verdict"],
                "confidence": r["confidence"],
                "independent_sources": r["independent_sources"],
                "citations": [
                    {"url": e.get("url"), "source": e.get("source"), "quote": e.get("passage")}
                    for e in r["evidence"]
                ],
                "note": r["reason"],
            }
            for r in results
        ],
    }
