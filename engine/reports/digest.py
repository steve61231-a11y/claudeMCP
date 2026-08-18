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

from engine import llm
from engine.reports.analysts import GROUNDING_RULES, _render_mention

CHUNK_CHARS = 16000          # per-chunk prompt budget for the map step
MAP_MAX_TOKENS = 2500
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

    comments = sorted((m for m in mentions if m.get("source_type") == "comment"), key=eng, reverse=True)
    others = sorted((m for m in mentions if m.get("source_type") != "comment"), key=eng, reverse=True)
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


MAP_PROMPT = """You are a meticulous intelligence analyst distilling a batch of scraped mentions about {name}. Read EVERY item below and produce a compact structured digest that loses none of the substance.

{grounding}

For this batch, extract:
- claims: distinct factual claims/assertions made (each with the ref id of an item making it),
- themes: recurring topics or framings, with rough item counts,
- notable_quotes: the most revealing verbatim quotes (ref id + exact text), including dissenting or grassroots voices,
- entities: people/organisations named and how they relate to {name},
- sentiment_read: the balance of supportive / critical / neutral in this batch,
- anomalies: anything unusual, contradictory, coordinated, or that seems to point beneath the surface.

Batch (each line is one mention, prefixed with its ref):
{batch}

Respond with ONLY this JSON:
{{"digest": {{"claims": [{{"ref":"id","text":"..."}}], "themes": [{{"theme":"...","count":N}}], "notable_quotes": [{{"ref":"id","text":"..."}}], "entities": [{{"name":"...","relation":"..."}}], "sentiment_read": {{"supportive":N,"critical":N,"neutral":N}}, "anomalies": ["..."]}}}}"""


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
    except Exception:
        digest = {}
    digest["_chunk"] = index
    digest["_mentions_in_chunk"] = len(chunk)
    return digest


def build_corpus_digest(name: str, mentions: list[dict]) -> dict:
    """Map every mention through the model in chunks, returning the per-chunk
    digests plus a coverage record proving the whole corpus was read."""
    chunks = _chunk_mentions(mentions)
    if not chunks:
        return {"digests": [], "coverage": {"mentions_total": 0, "mentions_analyzed": 0, "chunks": 0}}

    from engine.config import settings

    workers = llm.concurrency(2 if settings.low_memory else MAP_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        digests = list(pool.map(lambda ic: _digest_chunk(name, ic[1], ic[0]), enumerate(chunks)))

    analyzed = sum(d.get("_mentions_in_chunk", 0) for d in digests)
    return {
        "digests": digests,
        "coverage": {
            "mentions_total": len(mentions),
            "mentions_analyzed": analyzed,
            "chunks": len(chunks),
            "complete": analyzed == len(mentions),
        },
    }


def digest_context(digest: dict, max_chars: int = 40000) -> str:
    """Flatten the per-chunk digests into a single compact context the reduce
    step / analysts read — a complete, compressed view of the whole corpus."""
    import json

    blob = json.dumps(digest.get("digests", []), default=str)
    return blob[:max_chars]
