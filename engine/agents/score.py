"""Batched per-item scoring — sentiment, stance and topic for the WHOLE corpus.

The old path spent one LLM round-trip per mention. At a few hundred mentions
that is slow; at the thousands a comprehensive sweep produces it is impossible,
which is why the pipeline capped per-mention work at a top-N slice and quietly
left the long tail unread. A cap like that is invisible in the output but
decisive in the conclusions: whatever sat in the unread tail simply never
existed as far as the report was concerned.

Batching removes the reason the cap existed. One call carries ~25 items, so a
1,000-item corpus costs ~40 calls instead of 1,000 — cheap enough on the bulk
model to score everything, every run. Items keep their identity through the
batch (positional refs are validated on the way back), and anything the model
fails to answer for is simply left unscored so a later run retries it rather
than a wrong value being written.
"""

from concurrent.futures import ThreadPoolExecutor

from engine import llm
from engine.config import settings

_MAX_ITEM_CHARS = 600
_BATCH_WORKERS = 4

VALID_SENTIMENTS = {"positive", "neutral", "negative"}

SCORE_PROMPT = """You are analysing public commentary about {subject} for an intelligence file.

For EACH numbered item below, judge:
  - sentiment: positive | neutral | negative — the tone TOWARD {subject}
    specifically, not the general mood of the text,
  - intensity: 1 (mild) to 5 (extreme),
  - stance: support | attack | concern | praise | neutral_report,
  - topic: a short (2-4 word) label for what the item is actually about,
  - language: the ISO code of the item's language (en, sw, ...). Kenyan text
    often mixes English, Swahili and Sheng — label the dominant one.

Judge only what the text says. If an item is not about {subject} at all, use
sentiment "neutral" and topic "off_topic".

Items:
{batch}

Respond with ONLY this JSON, one entry per item, keeping the numbers:
{{"scores": [{{"i": 1, "sentiment": "neutral", "intensity": 3, "stance": "neutral_report", "topic": "budget debate", "language": "en"}}]}}"""


def _score_batch(subject: str, items: list[tuple[str, str]]) -> dict[str, dict]:
    """Score one batch. Returns {item_id: score}; missing ids are left unscored."""
    lines = []
    for position, (_, text) in enumerate(items, start=1):
        snippet = (text or "").replace("\n", " ")[:_MAX_ITEM_CHARS]
        lines.append(f"[{position}] {snippet}")
    batch = "\n".join(lines)

    try:
        result = llm.call_json_untrusted(
            SCORE_PROMPT.format(subject=subject, batch=batch),
            batch,
            expected_keys={"scores"},
            max_tokens=min(8000, 120 * len(items) + 400),
            max_untrusted_chars=len(batch) + 1000,
            model=llm.bulk_model(),
        )
    except Exception:  # noqa: BLE001 — a failed batch is retried on the next run
        return {}

    scored: dict[str, dict] = {}
    for entry in result.get("scores") or []:
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(items):
            continue
        sentiment = str(entry.get("sentiment") or "neutral").lower()
        if sentiment not in VALID_SENTIMENTS:
            sentiment = "neutral"
        try:
            intensity = int(entry.get("intensity") or 3)
        except (TypeError, ValueError):
            intensity = 3
        scored[items[position - 1][0]] = {
            "sentiment": sentiment,
            "intensity": max(1, min(5, intensity)),
            "context_tag": (str(entry.get("stance")).strip() or None) if entry.get("stance") else None,
            "topic": (str(entry.get("topic")).strip() or None) if entry.get("topic") else None,
            "language": (str(entry.get("language")).strip()[:5] or None) if entry.get("language") else None,
            "confidence": 0.8,
            "source": "llm_batch",
        }
    return scored


def score_items(subject: str, items: list[tuple[str, str]]) -> dict[str, dict]:
    """Score every (id, text) pair. No cap: the whole corpus is analysed.

    Batches run concurrently; a batch that fails contributes nothing rather
    than poisoning the run, and its items stay unscored for a later retry.
    """
    if not items:
        return {}
    size = max(1, settings.agent_batch_size)
    batches = [items[i : i + size] for i in range(0, len(items), size)]

    scored: dict[str, dict] = {}
    workers = llm.concurrency(min(_BATCH_WORKERS, len(batches)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for partial in pool.map(lambda b: _score_batch(subject, b), batches):
            scored.update(partial)
    return scored
