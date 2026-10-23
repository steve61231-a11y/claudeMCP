"""The verified fact store — extract once, check mechanically, keep.

Phase 2 of `docs/FOUNDATION.md`. Until now every analyst built its own
context from its own sample of raw text and wrote whatever it liked over it;
nothing anchored a sentence in a report to words a source actually printed.
This reads each source ONCE and keeps only what can be pinned to it:

  * every record must carry a quote, and the quote must be found in that
    source's own text — word for word, after folding case, spacing and
    typographic quote marks. A paraphrase with no anchor is not stored; it is
    counted as `dropped_unverified` on the source's read record, so a model
    that invents is visible as a rate rather than invisible as a fact;
  * an allegation stays an allegation (`records._normalise` keeps the weaker
    of kind and status);
  * a source that was read and held nothing about the subject, and one the
    model failed on, are different rows (`FactExtraction.status`), and only
    the second is retried — the founding rule at the scale of one article;
  * whole articles are read, up to `FACT_ITEM_CHARS`, not their first
    paragraph.

It writes no prose and decides nothing a client reads. It is the material
the writers of phase 3 will be confined to.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from engine import llm, stages
from engine.config import settings
from engine.evidence import records as records_mod
from engine.runctx import carry

_WORKERS = 4
#: A quote shorter than this proves nothing — "the government said" is in
#: every article ever written.
MIN_QUOTE_WORDS = 4
_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", " ": " "})
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")


def _fold(text: str) -> str:
    text = (text or "").translate(_FOLD).lower()
    text = re.sub(r"[\"']", "", text)
    return re.sub(r"\s+", " ", text).strip(" .,;:")


def quote_in_source(quote: str, source: str) -> bool:
    """Is this quote the source's own words?

    Contiguous after folding — no word-run fallback, no fuzzy match. An
    ellipsis may join two pieces, each of which must appear, in order. This is
    deliberately stricter than the quote check the prose analysts use: a fact
    rests on it, not an illustration."""
    pieces = [_fold(p) for p in _ELLIPSIS.split(quote or "") if _fold(p)]
    if not pieces or sum(len(p.split()) for p in pieces) < MIN_QUOTE_WORDS:
        return False
    haystack = _fold(source)
    at = 0
    for piece in pieces:
        found = haystack.find(piece, at)
        if found < 0:
            return False
        at = found + len(piece)
    return True


def source_key(item: dict) -> str | None:
    """Which table an item came from — stated by the corpus builder, never
    inferred from source_type (see CLAUDE.md, '"article" is not a table')."""
    kind = item.get("kind")
    if kind not in ("mention", "document") or not item.get("id"):
        return None
    return f"{kind}:{item['id']}"


def _batches(items: list[dict], max_chars: int, budget: int = 16000, most: int = 8):
    batch, used = [], 0
    for item in items:
        size = len(records_mod.item_text(item, max_chars))
        if batch and (used + size > budget or len(batch) >= most):
            yield batch
            batch, used = [], 0
        batch.append(item)
        used += size
    if batch:
        yield batch


def _read(subject: str, batch: list[dict], max_chars: int) -> list[tuple[dict, list[dict] | None, str | None]]:
    """(item, raw records or None, error) for each item. A failed batch splits
    so one hostile or oversized item cannot cost its neighbours."""
    try:
        entries = records_mod.extract_strict(subject, batch, max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001
        if len(batch) > 1:
            middle = len(batch) // 2
            return _read(subject, batch[:middle], max_chars) + _read(subject, batch[middle:], max_chars)
        stages.current().failed("fact_extraction", exc)
        return [(batch[0], None, f"{type(exc).__name__}: {exc}"[:300])]
    by_item: dict[int, list[dict]] = {i: [] for i in range(len(batch))}
    for entry in entries:
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if 1 <= position <= len(batch):
            by_item[position - 1].append(entry)
    return [(batch[i], by_item[i], None) for i in range(len(batch))]


def _hash(statement: str) -> str:
    return hashlib.sha256(_fold(statement).encode()).hexdigest()


def verify(item: dict, entries: list[dict], max_chars: int) -> tuple[list[dict], int]:
    """The records whose quote is in the text the model was shown, and how
    many were not."""
    text = records_mod.item_text(item, max_chars)
    kept, dropped, seen = [], 0, set()
    for entry in entries:
        statement = str(entry.get("statement") or "").strip()
        quote = str(entry.get("quote") or "").strip()
        if not statement:
            continue
        if not quote_in_source(quote, text):
            dropped += 1
            continue
        digest = _hash(statement)
        if digest in seen:
            continue
        seen.add(digest)
        kind, status = records_mod._normalise(entry)
        kept.append({"kind": kind, "status": status, "statement": statement[:600],
                     "quote": quote[:800], "statement_hash": digest,
                     "actor": str(entry.get("actor") or "").strip()[:120] or None,
                     "topic": str(entry.get("topic") or "").strip()[:80] or None,
                     "sentiment": str(entry.get("sentiment") or "").strip().lower()[:10] or None})
    return kept, dropped


def extract_and_store(db, politician, corpus: list[dict], limit: int | None = None) -> dict:
    """Read every source in `corpus` not yet read, keep what verifies, and say
    what happened. Commits; idempotent across runs."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from engine.db.models import Fact, FactExtraction

    max_chars = max(500, int(settings.fact_item_chars))
    keyed = {source_key(item): item for item in corpus if source_key(item)}
    done = {key for (key,) in db.query(FactExtraction.source_key).filter(
        FactExtraction.source_key.in_(list(keyed)), FactExtraction.status == "read")} if keyed else set()
    # Text-bearing items first: a full article holds more than a reaction.
    todo = sorted((item for key, item in keyed.items() if key not in done),
                  key=lambda m: (not m.get("full_text"), -len(m.get("text") or "")))
    todo = todo[: (limit if limit is not None else settings.fact_extraction_max_items)]
    stats = {"sources_in_corpus": len(keyed), "already_read": len(done), "read": 0,
             "failed": 0, "facts": 0, "dropped_unverified": 0, "by_kind": {}, "by_status": {}}
    if not todo:
        return stats

    batches = list(_batches(todo, max_chars))
    with ThreadPoolExecutor(max_workers=max(1, llm.concurrency(min(_WORKERS, len(batches))))) as pool:
        results = [r for batch in pool.map(carry(lambda b: _read(politician.name, b, max_chars)), batches)
                   for r in batch]

    model = llm.bulk_model()
    now = datetime.utcnow()
    for item, entries, error in results:
        key = source_key(item)
        text = item.get("text") or ""
        row = {"politician_id": politician.id, "source_key": key, "model": model,
               "chars_total": len(text), "chars_read": min(len(text), max_chars),
               "extracted_at": now}
        if entries is None:
            row.update(status="failed", facts=0, dropped_unverified=0, error=error)
            stats["failed"] += 1
        else:
            kept, dropped = verify(item, entries, max_chars)
            kind, ident = key.split(":", 1)
            posted = item.get("posted_at")
            for fact in kept:
                db.execute(pg_insert(Fact).values(
                    politician_id=politician.id, source_key=key,
                    mention_id=ident if kind == "mention" else None,
                    document_id=ident if kind == "document" else None,
                    published_at=None if item.get("date_estimated") else posted,
                    model=model, extracted_at=now, **fact,
                ).on_conflict_do_nothing(constraint="uq_fact_source_statement"))
                stats["by_kind"][fact["kind"]] = stats["by_kind"].get(fact["kind"], 0) + 1
                stats["by_status"][fact["status"]] = stats["by_status"].get(fact["status"], 0) + 1
            row.update(status="read", facts=len(kept), dropped_unverified=dropped, error=None)
            stats["read"] += 1
            stats["facts"] += len(kept)
            stats["dropped_unverified"] += dropped
        db.execute(pg_insert(FactExtraction).values(**row).on_conflict_do_update(
            index_elements=["source_key"],
            set_={k: row[k] for k in ("status", "facts", "dropped_unverified", "chars_read",
                                      "chars_total", "model", "error", "extracted_at")}))
    db.commit()
    proposed = stats["facts"] + stats["dropped_unverified"]
    stats["verified_share"] = round(100 * stats["facts"] / proposed, 1) if proposed else None
    return stats


# ---------------------------------------------------------------------------
# One story, many outlets
# ---------------------------------------------------------------------------

#: How firmly a status establishes something. A group takes its WEAKEST
#: member's: if one outlet reports as an allegation what another states as
#: fact, the honest summary is that it is alleged.
_STRENGTH = {"reported": 4, "inferred": 3, "unresolved": 2, "alleged": 1, "opinion": 0}
_STOP = {"that", "this", "with", "from", "have", "were", "said", "says", "will",
         "would", "their", "they", "about", "after", "over", "into", "been", "which"}
SAME_STORY = 0.5


def _content(statement: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _fold(statement)) if len(w) > 3 and w not in _STOP}


def corroborate(rows: list[dict], threshold: float = SAME_STORY) -> list[dict]:
    """Group facts that say the same thing, and count who independently said it.

    `rows` carry statement, quote, kind, status, publisher, url, published_at,
    and optionally source_id and source_text. Same story = the statements
    share at least `threshold` of their content words (Jaccard). An opinion is
    never grouped with a statement of fact.

    Independence counts ORIGINS: sources from one publisher are one origin,
    and so are sources whose texts are near-duplicates (`independence.py`) —
    the same wire story under two mastheads is one report carried twice, not
    two newsrooms confirming it. Without source text, publishers alone
    decide."""
    groups: list[dict] = []
    ordered = sorted(rows, key=lambda r: (r.get("published_at") is None, str(r.get("published_at") or "")))
    for row in ordered:
        words = _content(row.get("statement") or "")
        family = row.get("kind") == "opinion"
        best, best_score = None, 0.0
        for group in groups:
            if group["_family"] != family:
                continue
            union = words | group["_words"]
            score = len(words & group["_words"]) / len(union) if union else 0.0
            if score > best_score:
                best, best_score = group, score
        if best is None or best_score < threshold:
            best = {"_family": family, "_words": set(words), "members": []}
            groups.append(best)
        best["members"].append(row)
        best["_words"] |= words

    out = []
    for group in groups:
        members = group["members"]
        weakest = min(members, key=lambda m: _STRENGTH.get(m.get("status"), 0))
        dates = [str(m["published_at"]) for m in members if m.get("published_at")]
        origins, syndicated = _origins(members)
        out.append({
            "statement": weakest.get("statement"),
            "kind": weakest.get("kind"),
            "status": weakest.get("status"),
            "independent_sources": origins,
            "publishers": len({m.get("publisher") or "unknown" for m in members}),
            "syndicated": syndicated,
            "first_seen": min(dates) if dates else None,
            "last_seen": max(dates) if dates else None,
            "sources": [{k: m.get(k) for k in ("publisher", "url", "published_at", "quote", "status")}
                        for m in members],
        })
    # Most independently corroborated first; among equals, most recent first.
    out.sort(key=lambda g: g["last_seen"] or "", reverse=True)
    out.sort(key=lambda g: g["independent_sources"], reverse=True)
    return out


def _origins(members: list[dict]) -> tuple[int, bool]:
    """(distinct origins, whether any two publishers carried the same copy)."""
    from engine.evidence.independence import group_duplicates

    parent = list(range(len(members)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        parent[find(a)] = find(b)

    by_publisher: dict[str, int] = {}
    for i, m in enumerate(members):
        key = m.get("publisher") or "unknown"
        if key in by_publisher:
            union(i, by_publisher[key])
        else:
            by_publisher[key] = i
    texts = [{"id": i, "text": m.get("source_text"), "posted_at": None}
             for i, m in enumerate(members) if m.get("source_text")]
    syndicated = False
    for group in group_duplicates(texts):
        ids = group.mention_ids
        if len({members[i].get("publisher") for i in ids}) > 1:
            syndicated = True
        for other in ids[1:]:
            union(ids[0], other)
    return len({find(i) for i in range(len(members))}), syndicated
