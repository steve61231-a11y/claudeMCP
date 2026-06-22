import hashlib
import re
import unicodedata
from collections import Counter
from datetime import datetime

from engine.ingestion.base import IngestedMention

SPAM_LINK_DENSITY_THRESHOLD = 0.3
SPAM_BURST_THRESHOLD = 8  # same author posting more than this within the batch


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def content_hash(author_handle: str, text: str) -> str:
    return hashlib.sha256(f"{author_handle}:{normalize_text(text).lower()}".encode()).hexdigest()


def link_density(text: str) -> float:
    words = text.split()
    if not words:
        return 0.0
    links = sum(1 for w in words if w.startswith("http://") or w.startswith("https://"))
    return links / len(words)


def clean_mentions(mentions: list[IngestedMention]) -> list[dict]:
    """Dedupes by content hash and flags spam via posting-frequency / link-density heuristics."""
    seen_hashes: set[str] = set()
    author_counts = Counter(m["author_handle"] for m in mentions)

    cleaned = []
    for mention in mentions:
        text = normalize_text(mention["text"])
        h = content_hash(mention["author_handle"], text)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)

        is_spam = (
            link_density(text) > SPAM_LINK_DENSITY_THRESHOLD
            or author_counts[mention["author_handle"]] > SPAM_BURST_THRESHOLD
        )

        cleaned.append({**mention, "text": text, "content_hash": h, "is_spam": is_spam})
    return cleaned
