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

from engine import llm, stages
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
# Passages per extraction call, and claims per adjudication call.
#
# Both stages were one LLM round-trip per item, which is affordable only while
# the item counts are small. They stopped being small: `risks`, `opportunities`
# and `trends` are LISTS, every element becomes its own extraction target, and
# raising those sections from "3-5 items" to "6-12" tripled the extraction
# calls and multiplied the judgements downstream. On a serialised backend that
# is most of the wall-clock of a report.
#
# Adjudication batches are smaller than extraction batches because each claim
# carries its own retrieved evidence, so the prompt grows much faster per item.
_EXTRACT_BATCH = 10
_ADJUDICATE_BATCH = 6


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
    except Exception as exc:  # noqa: BLE001
        stages.current().failed("claim_extraction", exc)
        return []
    claims = [str(c).strip() for c in (result.get("claims") or []) if str(c).strip()]
    return claims[:max_claims]


BATCH_EXTRACT_PROMPT = """You are auditing an intelligence report before it is released.

For EACH numbered passage below, break it into ATOMIC factual claims — each a single,
self-contained, checkable assertion about the subject or the world. Keep every claim
attached to the number of the passage it came from.

Skip anything that is opinion, recommendation or analysis rather than a factual
assertion. A passage with no checkable claims simply has no entries.

Passages:
{batch}

Respond with ONLY this JSON, keeping the numbers:
{{"claims": [{{"i": 1, "text": "a single checkable assertion"}}, {{"i": 1, "text": "another"}}, {{"i": 2, "text": "..."}}]}}"""


BATCH_ADJUDICATE_PROMPT = """You are a fact-checker. For EACH numbered claim below, judge it
ONLY against the evidence listed under that claim. Do not use outside knowledge.

  verified     — the evidence directly supports the claim,
  contradicted — the evidence directly contradicts it,
  unverified   — the evidence neither supports nor contradicts it.

`support` lists the evidence numbers (within that claim's own list) that carry the verdict.

{batch}

Respond with ONLY this JSON, keeping the claim numbers:
{{"verdicts": [{{"i": 1, "verdict": "verified|contradicted|unverified", "support": [1], "reason": "one sentence"}}]}}"""


def _render_evidence(evidence: list[dict]) -> str:
    return "\n".join(
        f"    [{i}] ({e.get('source') or 'unknown source'}) {e.get('passage', '')[:400]}"
        for i, e in enumerate(evidence, start=1)
    )


def extract_claims_batch(passages: list[str]) -> dict[int, list[str]]:
    """Atomic claims for several passages in one call. {position: [claims]}."""
    lines = []
    for position, passage in enumerate(passages, start=1):
        lines.append(f"[{position}] {(passage or '')[:6000]}")
    batch = "\n\n".join(lines)
    try:
        result = llm.call_json(
            BATCH_EXTRACT_PROMPT.format(batch=batch),
            max_tokens=llm.budget_for(220 * len(passages) + 500),
        )
    except Exception as exc:  # noqa: BLE001 — an unextracted passage is simply unchecked
        stages.current().failed(f"claim_extraction[{len(passages)}]", exc)
        return {}

    out: dict[int, list[str]] = {}
    for entry in result.get("claims") or []:
        if not isinstance(entry, dict):
            continue
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(passages):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        bucket = out.setdefault(position, [])
        if len(bucket) < _MAX_CLAIMS_PER_SECTION:
            bucket.append(text)
    return out


def adjudicate_batch(items: list[tuple[str, list[dict]]]) -> dict[int, dict]:
    """Judge several claims, each against its own evidence, in one call.

    Returns {position: outcome}. A claim the model does not answer for is
    absent, and the caller leaves it unverified — the judge failing must never
    be able to UPGRADE a claim's status.
    """
    blocks = []
    for position, (claim, evidence) in enumerate(items, start=1):
        blocks.append(f"Claim [{position}]: {claim}\n  Evidence for claim [{position}]:\n"
                      + (_render_evidence(evidence) or "    (none)"))
    batch = "\n\n".join(blocks)
    try:
        result = llm.call_json_untrusted(
            BATCH_ADJUDICATE_PROMPT.format(batch=batch),
            batch,
            expected_keys={"verdicts"},
            max_tokens=llm.budget_for(220 * len(items) + 400),
            max_untrusted_chars=len(batch) + 1000,
        )
    except Exception as exc:  # noqa: BLE001
        # These claims stay UNVERIFIED, which is right — but a verification
        # that never ran must not read as a report with nothing to check.
        stages.current().failed(f"claim_adjudication[{len(items)}]", exc)
        return {}

    out: dict[int, dict] = {}
    for entry in result.get("verdicts") or []:
        if not isinstance(entry, dict):
            continue
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(items):
            continue
        verdict = str(entry.get("verdict") or UNVERIFIED).lower()
        if verdict not in (VERIFIED, CONTRADICTED, UNVERIFIED):
            verdict = UNVERIFIED
        evidence = items[position - 1][1]
        support = []
        for index in entry.get("support") or []:
            try:
                slot = int(index)
            except (TypeError, ValueError):
                continue
            if 1 <= slot <= len(evidence):
                support.append(slot)
        out[position] = {"verdict": verdict, "support": support,
                         "reason": str(entry.get("reason") or "")[:300]}
    return out


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


def _judge(section: str, claim_text: str, found: list[dict],
           credibility: dict[str, float] | None = None) -> dict:
    """Adjudicate one already-retrieved claim. Pure LLM work, so it is safe to
    run concurrently — database access happens on the caller's session."""
    return _settle(section, claim_text, found, credibility, adjudicate(claim_text, found))


def _settle(section: str, claim_text: str, found: list[dict],
            credibility: dict[str, float] | None, outcome: dict) -> dict:
    """Turn a verdict into the stored result: supporting evidence, independent
    source count and confidence. Shared so the batched and single-claim paths
    can never disagree about what a verdict means."""
    supporting = [found[i - 1] for i in outcome["support"]] or (
        found[:2] if outcome["verdict"] == VERIFIED else []
    )
    independent = evidence_store.independent_source_count(supporting)
    confidence = _confidence(outcome["verdict"], independent)
    # Corroboration count says HOW MANY sources; credibility says how much each
    # is worth. Three weak sources should not outrank two strong ones.
    if credibility:
        from engine.agents.credibility import weighted_confidence

        keys = [e.get("source") or e.get("url") or "" for e in supporting]
        confidence = weighted_confidence(confidence, [k for k in keys if k], credibility)
    return {
        "section": section,
        "text": claim_text,
        "verdict": outcome["verdict"],
        "reason": outcome["reason"],
        "evidence": supporting,
        "independent_sources": independent,
        "confidence": confidence,
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

    # Extraction, batched. `risks`, `opportunities` and `trends` are lists, so
    # every element is its own target — dozens of round-trips where a handful
    # of calls will do.
    claims: list[tuple[str, str]] = []
    extract_batches = [targets[i : i + _EXTRACT_BATCH] for i in range(0, len(targets), _EXTRACT_BATCH)]
    with ThreadPoolExecutor(max_workers=llm.concurrency(min(_WORKERS, len(extract_batches) or 1))) as pool:
        extracted = list(pool.map(
            lambda batch: (batch, extract_claims_batch([passage for _, passage in batch])),
            extract_batches,
        ))
    for batch, per_position in extracted:
        for position, texts in sorted(per_position.items()):
            section = batch[position - 1][0]
            for claim_text in texts:
                claims.append((section, claim_text))

    if not claims:
        # "0 checked" is ambiguous: a report with no factual assertions, or an
        # extractor that died. Say which, so a verification section reading
        # "nothing to check" cannot mean "the checker was down".
        extraction_failed = any(r.name.startswith("claim_extraction")
                                for r in stages.current().failures)
        return {"checked": 0, "verified": 0, "unverified": 0, "contradicted": 0,
                "claims": [],
                "note": ("claim extraction failed, so nothing was checked — this is not a "
                         "report without factual assertions"
                         if extraction_failed else
                         "no checkable factual assertions were found in the report prose")}

    # Retrieval is fast SQL on the caller's session (sessions are not
    # thread-safe); only the slow LLM adjudication is parallelised.
    retrieved = [
        (section, text, evidence_store.retrieve_for_claim(db, politician.id, text))
        for section, text in claims
    ]

    # Look up how much each backing source is actually worth, once, on the
    # caller's session — the judging threads then work from plain data.
    from engine.agents.credibility import credibility_for

    source_keys = {
        (e.get("source") or e.get("url") or "")
        for _, _, found in retrieved
        for e in found
    }
    credibility = credibility_for(db, [k for k in source_keys if k])

    # Adjudication, batched. A claim with no evidence at all is unverified by
    # definition, so it never costs a call — filter those out before batching
    # rather than paying to be told what we already know.
    judged: list[dict] = []
    needs_judging = [(i, c) for i, c in enumerate(retrieved) if c[2]]
    for section, claim_text, found in (c for c in retrieved if not c[2]):
        judged.append(_settle(section, claim_text, found, credibility,
                              {"verdict": UNVERIFIED, "support": [],
                               "reason": "no supporting evidence found in the corpus"}))

    batches = [needs_judging[i : i + _ADJUDICATE_BATCH]
               for i in range(0, len(needs_judging), _ADJUDICATE_BATCH)]
    workers = llm.concurrency(min(_WORKERS, len(batches) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(
            lambda batch: (batch, adjudicate_batch([(c[1], c[2]) for _, c in batch])),
            batches,
        ))
    for batch, per_position in outcomes:
        for position, (_, claim) in enumerate(batch, start=1):
            section, claim_text, found = claim
            # A claim the judge did not answer for stays unverified: a failed
            # judgement must never be able to UPGRADE a claim's status.
            outcome = per_position.get(position) or {
                "verdict": UNVERIFIED, "support": [], "reason": "verification unavailable"}
            judged.append(_settle(section, claim_text, found, credibility, outcome))
    results = judged

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
