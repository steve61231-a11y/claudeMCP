"""Whole-corpus map-reduce digestion — the guarantee that the AI reads EVERY
mention, not a truncated top-by-engagement slice.

The problem this solves: a single prompt can only hold ~tens of thousands of
characters, so feeding one big blob silently drops most of a 600+ mention
corpus. Analysts then reason over a fraction of the data and can miss the very
detail that reframes everything.

The method:
  MAP    — partition the FULL corpus into ordered chunks that each fit one
           prompt, and have the model distil each chunk into compact,
           ref-tagged observations (claims, themes, quotes, entities,
           anomalies, sentiment read). Every mention passes through the model
           in exactly one chunk, so coverage is total and provable.
  REDUCE — downstream analysts and the synthesizer read the DIGESTS (a
           complete, compressed representation of the whole corpus) instead of
           raw truncated text, so they reason over everything at once.

Coverage is counted and surfaced ("analysed N of N mentions across K chunks")
so the report can honestly claim it saw all the data. Grounding is preserved:
observations carry ref ids validated the same way quotes are.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from engine import llm, stages
from engine.reports.analysts import (DIGEST_CONTEXT_CHARS, GROUNDING_RULES,
                                     _render_mention)

CHUNK_CHARS = 16000          # per-chunk prompt budget for the map step
# The map step is where corpus detail is either preserved or lost forever —
# every analyst downstream reads this digest, not the mentions. Compressing to
# 2500 tokens per chunk was the first of the two places depth was destroyed.
MAP_MAX_TOKENS = 4000
MAP_WORKERS = 6


def chunk_budget() -> int:
    """Characters of corpus per map-step call.

    Free providers meter requests per day rather than tokens, so on a
    large-context model raising this is what keeps a whole report inside the
    daily allowance: doubling it halves the number of calls.
    """
    from engine.config import settings

    return settings.llm_chunk_chars or CHUNK_CHARS


def _chunk_mentions(mentions: list[dict], budget: int | None = None) -> list[list[dict]]:
    """Partition ALL mentions into ordered, prompt-sized chunks. Comments and
    posts are interleaved so every chunk carries both official and grassroots
    voice. Nothing is dropped — the union of chunks is the whole corpus."""
    budget = budget or chunk_budget()

    def eng(m):
        e = m.get("engagement") or {}
        return sum(int(e.get(k) or 0) for k in ("views", "likes", "shares", "comments"))

    # Documents that name both halves of the question go first. Ordering by
    # engagement alone buried the handful of items that actually establish the
    # connection under a hundred background articles, in a later chunk that a
    # failed call could take out entirely.
    _POOL_RANK = {"core": 0, "principal_side": 1, "issue_side": 2}

    def rank(m):
        return (_POOL_RANK.get(m.get("evidence_pool"), 1), -eng(m))

    comments = sorted((m for m in mentions if m.get("source_type") == "comment"), key=rank)
    others = sorted((m for m in mentions if m.get("source_type") != "comment"), key=rank)
    ordered: list[dict] = []
    ci, pi = 0, 0
    while ci < len(comments) or pi < len(others):
        if ci < len(comments):
            ordered.append(comments[ci]); ci += 1
        ordered.extend(others[pi:pi + 2]); pi += 2

    chunks: list[list[dict]] = []
    cur: list[dict] = []
    used = 0
    for m in ordered:
        line_len = len(_render_mention(m)) + 1
        if cur and used + line_len > budget:
            chunks.append(cur)
            cur, used = [], 0
        cur.append(m)
        used += line_len
    if cur:
        chunks.append(cur)
    return chunks


MAP_PROMPT = """You are a meticulous intelligence analyst distilling a batch of scraped mentions about {name}. Read EVERY item below and produce a dense structured digest that loses none of the substance.

Compression is allowed. Omission is not. Everything downstream in this report reads YOUR digest and never sees these items again — a claim, quote, entity or anomaly you leave out is gone from the analysis for good. Extract every distinct one, not a representative sample.

{grounding}

For this batch, extract:
- claims: EVERY distinct factual claim/assertion made (each with the ref id of an item making it) — expect dozens in a full batch, not a handful,
- themes: every recurring topic or framing, with rough item counts,
- notable_quotes: the most revealing verbatim quotes (ref id + exact text), including dissenting or grassroots voices — take as many as carry information,
- entities: EVERY person/organisation/company/agency named, however briefly, and how they relate to {name},
- sentiment_read: the balance of supportive / critical / neutral in this batch,
- anomalies: anything unusual, contradictory, coordinated, or that seems to point beneath the surface.

Batch (each line is one mention, prefixed with its ref):
{batch}

Respond with ONLY this JSON. The example shows the FORM, not the QUANTITY — return as many elements as the batch contains:
{{"digest": {{"claims": [{{"ref":"id","text":"..."}}, {{"ref":"id","text":"..."}}, {{"ref":"id","text":"..."}}, {{"ref":"id","text":"..."}}], "themes": [{{"theme":"...","count":N}}, {{"theme":"...","count":N}}, {{"theme":"...","count":N}}], "notable_quotes": [{{"ref":"id","text":"..."}}, {{"ref":"id","text":"..."}}, {{"ref":"id","text":"..."}}], "entities": [{{"name":"...","relation":"..."}}, {{"name":"...","relation":"..."}}, {{"name":"...","relation":"..."}}, {{"name":"...","relation":"..."}}], "sentiment_read": {{"supportive":N,"critical":N,"neutral":N}}, "anomalies": ["...", "..."]}}}}"""


def _digest_chunk(name: str, chunk: list[dict], index: int) -> dict:
    batch = "\n".join(_render_mention(m) for m in chunk)
    try:
        result = llm.call_json(
            MAP_PROMPT.format(name=name, grounding=GROUNDING_RULES, batch=batch[:chunk_budget() + 4000]),
            max_tokens=MAP_MAX_TOKENS,
            # The map step is the highest-volume call in the system — one per
            # chunk of the whole corpus — and it is mechanical extraction, which
            # is exactly what the bulk tier is for. The reduce step and the
            # analysts that read this digest keep the strong model.
            model=llm.bulk_model(),
        )
        digest = result.get("digest", {})
        failed = None
    except Exception as exc:  # noqa: BLE001 — one dead chunk must not stop the map
        digest = {}
        failed = f"{type(exc).__name__}: {exc}"[:200]
        stages.current().failed(f"digest_chunk:{index}", exc)
    digest["_chunk"] = index
    digest["_mentions_in_chunk"] = len(chunk)
    # Whether this chunk was actually READ. Without it the coverage record
    # below counts a chunk that failed as one that was analysed.
    digest["_failed"] = failed
    return digest


def raw_corpus_context(mentions: list[dict], max_chars: int) -> str:
    """The documents themselves, rendered for a prompt.

    The digest exists to COMPRESS a corpus too large to send. When the corpus
    already fits, compressing it is pure loss: it spends model calls, throws
    away the wording, and — the reason this exists — it is a single point of
    failure in front of everything. A run that collected 23 documents and
    reported "analysed 0 of 23" had every one of them sitting in memory while
    the analysts were handed an empty digest and produced nothing.
    """
    header = ("THESE ARE THE SOURCE DOCUMENTS THEMSELVES, not a digest of them. "
              "One per line, each prefixed with its ref id. Quote from them directly.\n")
    lines, used = [], len(header)
    for mention in mentions:
        line = _render_mention(mention)
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return header + "\n".join(lines)


def fits_without_compression(mentions: list[dict], max_chars: int) -> bool:
    """Would the whole corpus fit in one analyst window, uncompressed?"""
    total = 0
    for mention in mentions:
        total += len(_render_mention(mention)) + 1
        if total > max_chars:
            return False
    return True


def build_corpus_digest(name: str, mentions: list[dict]) -> dict:
    """Map every mention through the model in chunks, returning the per-chunk
    digests plus a coverage record proving the whole corpus was read.

    A corpus small enough to send whole skips the model entirely.
    """
    chunks = _chunk_mentions(mentions)
    if not chunks:
        return {"digests": [], "coverage": {"mentions_total": 0, "mentions_analyzed": 0, "chunks": 0}}

    budget = int(DIGEST_CONTEXT_CHARS)
    if fits_without_compression(mentions, budget):
        # Nothing to compress, so nothing to fail. The analysts read the
        # documents, which is strictly better than reading a summary of them.
        return {
            "digests": [],
            "raw": raw_corpus_context(mentions, budget),
            "coverage": {
                "mentions_total": len(mentions), "mentions_analyzed": len(mentions),
                "chunks": 0, "chunks_failed": 0, "complete": True, "mode": "raw",
                "note": ("The corpus fitted in one window, so the analysts read every "
                         "document in full rather than a compressed digest of them."),
            },
        }

    from engine.config import settings

    workers = llm.concurrency(2 if settings.low_memory else MAP_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        digests = list(pool.map(lambda ic: _digest_chunk(name, ic[1], ic[0]), enumerate(chunks)))

    # Count only what was actually read. `_mentions_in_chunk` was set on every
    # chunk including the failed ones, so a run where EVERY chunk 404'd still
    # reported "mentions_analyzed: 188, complete: true". The report then told
    # its reader the analyst had read every item while it had read none — the
    # most damaging single lie this pipeline was capable of, because it is the
    # number that makes the thin sections beneath it look like the truth.
    read = [d for d in digests if not d.get("_failed")]
    # Every pass failed. The documents are still here; handing the analysts an
    # empty digest instead is how a run reports "analysed 0 of 23" and then
    # renders every section blank.
    if not read:
        stages.current().failed(
            "digest", f"all {len(chunks)} passes failed; falling back to raw documents")
        return {
            "digests": digests,
            "raw": raw_corpus_context(mentions, int(DIGEST_CONTEXT_CHARS)),
            "coverage": {
                "mentions_total": len(mentions),
                "mentions_analyzed": 0, "chunks": len(chunks),
                "chunks_failed": len(chunks), "complete": False, "mode": "raw_fallback",
                "note": (f"All {len(chunks)} passes over the corpus failed, so "
                         f"{len(mentions)} mentions were never read by the map step. The "
                         "analysts were given the documents themselves instead, up to what "
                         "one window holds — nothing was summarised first."),
            },
        }
    failed_chunks = [d for d in digests if d.get("_failed")]
    analyzed = sum(d.get("_mentions_in_chunk", 0) for d in read)
    coverage = {
        "mentions_total": len(mentions),
        "mentions_analyzed": analyzed,
        "chunks": len(chunks),
        "chunks_failed": len(failed_chunks),
        "complete": analyzed == len(mentions) and not failed_chunks,
    }
    if failed_chunks:
        coverage["note"] = (
            f"{len(failed_chunks)} of {len(chunks)} passes over the corpus failed, so "
            f"{len(mentions) - analyzed} mentions were never read. Everything below rests "
            "on the remainder.")
    return {"digests": digests, "coverage": coverage}


def digest_context(digest: dict, max_chars: int = 40000) -> str:
    """Flatten the per-chunk digests into a single compact context the reduce
    step / analysts read — a complete, compressed view of the whole corpus."""
    import json

    # A raw corpus, when the digest was skipped or every pass failed.
    raw = digest.get("raw")
    if raw:
        return raw[:max_chars]
    blob = json.dumps(digest.get("digests", []), default=str)
    return blob[:max_chars]
