from collections import defaultdict
from datetime import datetime

import numpy as np

from engine import llm

_embedder = None
_embedder_unavailable = False


def get_embedder():
    global _embedder, _embedder_unavailable
    if _embedder_unavailable:
        raise RuntimeError("Sentence embedder previously failed to load")
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        try:
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            _embedder_unavailable = True
            raise
    return _embedder


def embed_texts(texts: list[str]) -> np.ndarray:
    """Sentence-transformer embeddings, falling back to TF-IDF when the model
    can't be loaded (e.g. no network access to HuggingFace Hub). TF-IDF is a
    weaker semantic signal but keeps narrative clustering working instead of
    crashing the pipeline.
    """
    try:
        return get_embedder().encode(texts, show_progress_bar=False)
    except Exception:
        from sklearn.feature_extraction.text import TfidfVectorizer

        return TfidfVectorizer(max_features=512).fit_transform(texts).toarray()


def cluster_mentions(texts: list[str]) -> list[int]:
    """Returns a cluster label per text; -1 means noise (no clear narrative)."""
    import hdbscan

    if len(texts) < 3:
        return [-1] * len(texts)

    embeddings = embed_texts(texts)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=3, metric="euclidean")
    return clusterer.fit_predict(embeddings).tolist()


LABEL_PROMPT = """You are summarizing a cluster of social-media posts about a politician into a short narrative theme.

Sample posts:
{samples}

Respond with ONLY a JSON object: {{"label": "2-4 word theme", "description": "1 sentence description"}}
"""


def label_cluster(sample_texts: list[str]) -> dict:
    samples = "\n".join(f"- {t}" for t in sample_texts[:8])
    return llm.call_json(LABEL_PROMPT.format(samples=samples), max_tokens=200)


def build_narratives(mentions: list[dict]) -> list[dict]:
    """Groups mentions into narrative clusters, labels each via the LLM,
    and computes strength/growth metrics per cluster.

    `mentions` items need: id, text, posted_at, engagement (dict with likes/shares/comments).
    """
    if not mentions:
        return []

    texts = [m["text"] for m in mentions]
    labels = cluster_mentions(texts)

    clusters: dict[int, list[dict]] = defaultdict(list)
    for mention, label in zip(mentions, labels):
        if label == -1:
            continue
        clusters[label].append(mention)

    results = []
    for cluster_id, cluster_mentions_ in clusters.items():
        meta = label_cluster([m["text"] for m in cluster_mentions_])

        engagement_total = sum(
            m["engagement"].get("likes", 0) + m["engagement"].get("shares", 0) * 2 + m["engagement"].get("comments", 0)
            for m in cluster_mentions_
        )
        strength_score = len(cluster_mentions_) * 1.0 + engagement_total * 0.01

        timestamps = sorted(m["posted_at"] for m in cluster_mentions_)
        midpoint = timestamps[len(timestamps) // 2]
        first_half = sum(1 for t in timestamps if t < midpoint)
        second_half = len(timestamps) - first_half
        growth_rate = (second_half - first_half) / max(first_half, 1)

        results.append(
            {
                "label": meta.get("label", f"narrative-{cluster_id}"),
                "description": meta.get("description", ""),
                "mention_ids": [m["id"] for m in cluster_mentions_],
                "strength_score": strength_score,
                "growth_rate": growth_rate,
                "window_start": min(timestamps),
                "window_end": max(timestamps),
            }
        )
    return results
