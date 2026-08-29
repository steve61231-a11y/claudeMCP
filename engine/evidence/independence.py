"""Near-duplicate detection, and the count that actually matters.

A corpus of 661 mentions is not 661 pieces of evidence. A wire story runs in
twelve outlets under twelve bylines; a YouTube clip is re-uploaded by six
channels; a single tweet is quote-posted two hundred times. Counting those as
independent corroboration is the central way a monitoring system lies to its
reader — it manufactures consensus out of one source.

The existing dedup was an exact SHA-256 of `author:text`, which means it caught
literally nothing of this: the same wire copy under two outlet names hashes
differently, so both survived as "independent". This module does the job that
one was named for.

Method is deliberately not a model call. Near-duplicate detection is a solved
mechanical problem, it has to run over the whole corpus, and a judgement this
load-bearing should be reproducible and auditable rather than resampled from a
model every run.

  - Shingle each text into overlapping word trigrams.
  - MinHash those shingles into a fixed-width signature.
  - Band the signatures so candidate pairs are found in near-linear time.
  - Confirm candidates with true Jaccard over the shingle sets.
  - Union-find the confirmed pairs into duplicate groups.

What this catches: verbatim reprints, lightly sub-edited wire copy, reposts,
aggregator items lifted from a full article, the same headline under many
channels. What it does NOT catch: two journalists independently writing up the
same event in their own words. That is the correct boundary — a genuine rewrite
means an editor actually engaged with the story, which is what independence is
supposed to measure.

`independent_sources` then counts DISTINCT ORIGINS, not items: one per duplicate
group, and within a group the earliest item is treated as the origin.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

SHINGLE_SIZE = 2
SIGNATURE_SIZE = 96
BANDS = 24                  # rows per band = SIGNATURE_SIZE // BANDS = 4
NEAR_DUPLICATE_THRESHOLD = 0.60
_MIN_SHINGLES = 4           # below this a text is too short to fingerprint
_MIN_FOR_CONTAINMENT = 8    # overlap is unreliable on very short texts

_TOKEN_RE = re.compile(r"[a-z0-9']+")
_URL_RE = re.compile(r"https?://\S+")
# Boilerplate that rides along with almost every YouTube description and news
# footer. Left in, it makes unrelated items look alike and would merge stories
# that share nothing but a subscribe prompt.
_BOILERPLATE = re.compile(
    r"(subscribe|follow us|join this channel|get access to perks|"
    r"watch (?:live|more)|read more|click the link|all rights reserved|"
    r"copyright \d{4}|share this article)[^.]*", re.I)


def _tokens(text: str) -> list[str]:
    text = _URL_RE.sub(" ", text or "")
    text = _BOILERPLATE.sub(" ", text)
    return _TOKEN_RE.findall(text.lower())


def shingles(text: str, size: int = SHINGLE_SIZE) -> set[str]:
    """Overlapping word n-grams — the unit near-duplicate detection compares."""
    words = _tokens(text)
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def _hash(value: str, seed: int) -> int:
    return int.from_bytes(
        hashlib.blake2b(value.encode(), digest_size=8, salt=seed.to_bytes(16, "little")).digest(),
        "big",
    )


def signature(shingle_set: set[str], size: int = SIGNATURE_SIZE) -> tuple[int, ...]:
    """MinHash signature: the minimum hash per seed. Two texts sharing most of
    their shingles agree on most positions, whatever their length."""
    if not shingle_set:
        return tuple([0] * size)
    return tuple(min(_hash(s, seed) for s in shingle_set) for seed in range(size))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / (len(a) + len(b) - intersection)


def overlap(a: set[str], b: set[str]) -> float:
    """Containment: what share of the SHORTER text appears in the longer one.

    Jaccard punishes length differences, and the commonest syndication pattern
    in this corpus is exactly that — a three-line aggregator item lifted from a
    full article, or a headline reposted under a long video description. On
    those, Jaccard reads low while one text is plainly inside the other."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def similarity(a: set[str], b: set[str]) -> float:
    """How much two texts are the same story.

    Containment only counts once both texts are long enough for it to mean
    something; on a six-word item it fires on any shared phrase."""
    score = jaccard(a, b)
    if len(a) >= _MIN_FOR_CONTAINMENT and len(b) >= _MIN_FOR_CONTAINMENT:
        score = max(score, overlap(a, b))
    return score


class _Union:
    """Union-find. Duplication is transitive here on purpose: if A≈B and B≈C,
    all three are one story even when A and C drifted apart in editing."""

    def __init__(self, size: int):
        self._parent = list(range(size))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self._parent[max(ri, rj)] = min(ri, rj)


@dataclass
class DuplicateGroup:
    """One story, however many times it was published."""

    key: str
    mention_ids: list[str] = field(default_factory=list)
    origin_id: str | None = None
    origin_at: object = None
    origin_platform: str | None = None
    origin_author: str | None = None
    copies: int = 0

    @property
    def is_syndicated(self) -> bool:
        return self.copies > 1


def group_duplicates(mentions: list[dict],
                     threshold: float = NEAR_DUPLICATE_THRESHOLD) -> list[DuplicateGroup]:
    """Partition mentions into near-duplicate groups, earliest item as origin.

    Every mention lands in exactly one group, so the groups are a partition of
    the corpus and their count is the number of distinct stories in it."""
    if not mentions:
        return []

    shingle_sets = [shingles(m.get("text") or "") for m in mentions]
    signatures = [signature(s) for s in shingle_sets]
    union = _Union(len(mentions))

    # Banding: two items must agree on a whole band to be worth comparing. This
    # is what keeps the pass near-linear instead of quadratic on 661 items.
    rows = max(1, SIGNATURE_SIZE // BANDS)
    for band in range(BANDS):
        buckets: dict[tuple, list[int]] = {}
        start = band * rows
        for index, sig in enumerate(signatures):
            if len(shingle_sets[index]) < _MIN_SHINGLES:
                continue  # too short to fingerprint; never merged on similarity
            buckets.setdefault(sig[start : start + rows], []).append(index)
        for candidates in buckets.values():
            if len(candidates) < 2:
                continue
            first = candidates[0]
            for other in candidates[1:]:
                # Confirm with the real measure. Banding produces false pairs by
                # design; without this check, unrelated items sharing one band
                # would be declared the same story.
                if similarity(shingle_sets[first], shingle_sets[other]) >= threshold:
                    union.union(first, other)

    grouped: dict[int, list[int]] = {}
    for index in range(len(mentions)):
        grouped.setdefault(union.find(index), []).append(index)

    groups = []
    for root, members in grouped.items():
        items = [mentions[i] for i in members]
        dated = [m for m in items if m.get("posted_at") is not None]
        origin = min(dated, key=lambda m: m["posted_at"]) if dated else items[0]
        groups.append(DuplicateGroup(
            key=f"g{root}",
            mention_ids=[m.get("id") for m in items],
            origin_id=origin.get("id"),
            origin_at=origin.get("posted_at"),
            origin_platform=origin.get("platform"),
            origin_author=origin.get("author_handle"),
            copies=len(items),
        ))
    groups.sort(key=lambda g: g.copies, reverse=True)
    return groups


def independence(mentions: list[dict], threshold: float = NEAR_DUPLICATE_THRESHOLD) -> dict:
    """How much of this corpus is actually independent.

    `amplification` is the number a reader most needs and never gets: mentions
    per distinct story. At 1.0 every item is its own story; at 12.0 the corpus
    is one story repeated.
    """
    groups = group_duplicates(mentions, threshold)
    total = len(mentions)
    distinct = len(groups)
    outlets = {m.get("platform") for m in mentions if m.get("platform")}
    authors = {m.get("author_handle") for m in mentions if m.get("author_handle")}
    syndicated = [g for g in groups if g.is_syndicated]
    return {
        "mentions": total,
        "distinct_stories": distinct,
        "distinct_platforms": len(outlets),
        "distinct_authors": len(authors),
        "amplification": round(total / distinct, 2) if distinct else 0.0,
        "syndicated_groups": len(syndicated),
        "mentions_in_syndicated_groups": sum(g.copies for g in syndicated),
        "largest_group": syndicated[0].copies if syndicated else 1,
    }
