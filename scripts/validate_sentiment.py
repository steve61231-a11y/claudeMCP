"""Sentiment accuracy validation harness.

Samples stored mentions stratified by language, re-scores them with the LLM
as an independent second opinion, reports system-vs-LLM agreement per
language, and writes sentiment_validation.csv with an empty `human_label`
column. Once a bilingual human fills that column, re-run with --score-human
to get the number that matters: agreement with human raters, per language.

Usage:
  DATABASE_URL=... python scripts/validate_sentiment.py            # sample + LLM agreement
  DATABASE_URL=... python scripts/validate_sentiment.py --score-human  # after human labels are filled
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from concurrent.futures import ThreadPoolExecutor

from engine.db.session import SessionLocal
from engine.db.models import MentionSentiment, RawMention

CSV_PATH = Path(__file__).resolve().parent / "sentiment_validation.csv"
SAMPLE_PER_LANGUAGE = 34
LANGUAGES = ("en", "sw", "other")


def _language_bucket(lang: str | None) -> str:
    if lang in ("en", "eng", "english"):
        return "en"
    if lang in ("sw", "swa", "swahili", "sheng"):
        return "sw"
    return "other"


def sample_and_score():
    from engine.processing import sentiment as sentiment_module

    db = SessionLocal()
    try:
        rows = (
            db.query(RawMention, MentionSentiment)
            .join(MentionSentiment, MentionSentiment.mention_id == RawMention.id)
            .filter(RawMention.is_spam == 0)
            .all()
        )
    finally:
        db.close()

    buckets: dict[str, list] = defaultdict(list)
    for m, s in rows:
        if (m.text or "").strip():
            buckets[_language_bucket(m.language)].append((m, s))

    sample = []
    for lang in LANGUAGES:
        # Spread across the bucket rather than taking the newest slice.
        bucket = buckets.get(lang, [])
        step = max(1, len(bucket) // SAMPLE_PER_LANGUAGE)
        sample.extend((lang, m, s) for m, s in bucket[::step][:SAMPLE_PER_LANGUAGE])

    print(f"sampled {len(sample)} mentions "
          f"({', '.join(f'{lang}: {sum(1 for l, _, _ in sample if l == lang)}' for lang in LANGUAGES)})")

    def rescore(item):
        _, m, _ = item
        try:
            return sentiment_module.llm_sentiment_and_context(m.text)["sentiment"]
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        rescored = list(pool.map(rescore, sample))

    agree: dict[str, list[bool]] = defaultdict(list)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["mention_id", "language", "platform", "text", "system_label", "llm_recheck", "human_label"])
        for (lang, m, s), llm_label in zip(sample, rescored):
            if llm_label:
                agree[lang].append(s.sentiment == llm_label)
            writer.writerow([m.id, lang, m.platform, (m.text or "").replace("\n", " ")[:400], s.sentiment, llm_label or "", ""])

    print(f"\nwrote {CSV_PATH}")
    print("\nsystem vs independent LLM re-score agreement:")
    for lang in LANGUAGES:
        checks = agree.get(lang, [])
        if checks:
            print(f"  {lang}: {100 * sum(checks) / len(checks):.0f}% ({len(checks)} samples)")
    print("\nNEXT STEP (human): fill the human_label column (positive/neutral/negative), "
          "then run with --score-human for the real accuracy number.")


def score_human():
    per_lang: dict[str, list[bool]] = defaultdict(list)
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            human = (row.get("human_label") or "").strip().lower()
            if human in ("positive", "neutral", "negative"):
                per_lang[row["language"]].append(row["system_label"] == human)
    if not any(per_lang.values()):
        print("no human labels found in the CSV yet — fill the human_label column first")
        return
    print("system vs HUMAN agreement:")
    for lang, checks in sorted(per_lang.items()):
        print(f"  {lang}: {100 * sum(checks) / len(checks):.0f}% ({len(checks)} labeled)")


if __name__ == "__main__":
    if "--score-human" in sys.argv:
        score_human()
    else:
        sample_and_score()
