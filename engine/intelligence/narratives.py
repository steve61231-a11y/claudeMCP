import re
import threading
import time
from collections import defaultdict

import numpy as np

from engine import llm, stages

_embedder = None
_embedder_unavailable = False

#: Seconds to wait for the sentence-transformer to load before giving up on it.
#:
#: `SentenceTransformer("all-MiniLM-L6-v2")` DOWNLOADS the model on first use.
#: On a cold instance with a slow or blocked route to the HuggingFace hub that
#: call can take many minutes or never return, and it sits inside the narrative
#: stage — so a run that had already read the corpus and scored sentiment would
#: sit on "Narratives" indefinitely with nothing to show and no error. The
#: fallback for a FAILED load was always there; a load that never finishes is
#: not a failure, and nothing was watching for it.
EMBEDDER_LOAD_TIMEOUT = 90.0

_embedder_thread = None
_embedder_holder: dict = {}


def get_embedder():
    global _embedder, _embedder_unavailable
    if _embedder_unavailable:
        raise RuntimeError("Sentence embedder previously failed to load")
    from engine.config import settings

    if not settings.use_local_ml:
        # Skip torch/sentence-transformers on constrained deploys — embed_texts
        # falls back to TF-IDF (scikit-learn), keeping narrative clustering alive.
        _embedder_unavailable = True
        raise RuntimeError("local ML disabled (USE_LOCAL_ML=false)")
    if _embedder is None:
        # A previous run may have finished loading in the background after we
        # gave up waiting. Adopt it rather than paying for TF-IDF forever.
        if _embedder_holder.get("model") is not None:
            _embedder = _embedder_holder["model"]
            return _embedder
        if _embedder_holder.get("error"):
            _embedder_unavailable = True
            raise RuntimeError(str(_embedder_holder["error"]))

        _start_embedder_load()
        _embedder_thread.join(EMBEDDER_LOAD_TIMEOUT)
        if _embedder_holder.get("model") is not None:
            _embedder = _embedder_holder["model"]
            return _embedder
        if _embedder_holder.get("error"):
            _embedder_unavailable = True
            raise RuntimeError(str(_embedder_holder["error"]))
        # Still loading. Do not mark it permanently unavailable — it may well
        # arrive — but do not hold the run behind it either.
        raise TimeoutError(
            f"sentence embedder did not load within {EMBEDDER_LOAD_TIMEOUT:g}s "
            "(it downloads on first use); clustering continues without it")
    return _embedder


def _start_embedder_load() -> None:
    """Load the model on a daemon thread, once."""
    global _embedder_thread
    if _embedder_thread is not None and _embedder_thread.is_alive():
        return
    if _embedder_thread is not None:
        return  # finished: the holder already carries the result

    def _load():
        try:
            from sentence_transformers import SentenceTransformer

            _embedder_holder["model"] = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as exc:  # noqa: BLE001
            _embedder_holder["error"] = f"{type(exc).__name__}: {exc}"[:200]

    _embedder_thread = threading.Thread(target=_load, daemon=True,
                                        name="embedder-load")
    _embedder_thread.start()


def reset_embedder_state() -> None:
    """Test seam: forget everything learned about the embedder."""
    global _embedder, _embedder_unavailable, _embedder_thread
    _embedder = None
    _embedder_unavailable = False
    _embedder_thread = None
    _embedder_holder.clear()


def embed_texts(texts: list[str]) -> np.ndarray:
    """Sentence-transformer embeddings, falling back to TF-IDF when the model
    can't be loaded (e.g. no network access to HuggingFace Hub). TF-IDF is a
    weaker semantic signal but keeps narrative clustering working instead of
    crashing the pipeline.
    """
    try:
        embeddings = get_embedder().encode(texts, show_progress_bar=False)
    except Exception as exc:  # noqa: BLE001
        from sklearn.feature_extraction.text import TfidfVectorizer

        # TF-IDF clusters by shared WORDS, not shared meaning, so narratives get
        # noticeably worse — items about one event in different wording stop
        # grouping. Worth having; not worth having silently, because the report
        # then looks like the corpus had no coherent storylines.
        stages.current().record(
            "narrative_embeddings", stages.STATUS_OK,
            detail=f"sentence embeddings unavailable, fell back to TF-IDF "
                   f"(weaker clustering): {type(exc).__name__}: {exc}"[:200])
        return TfidfVectorizer(max_features=512).fit_transform(texts).toarray()
    return embeddings


def cluster_mentions(texts: list[str]) -> list[int]:
    """Returns a cluster label per text; -1 means noise (no clear narrative)."""
    import hdbscan

    if len(texts) < 3:
        return [-1] * len(texts)

    embeddings = embed_texts(texts)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=3, metric="euclidean")
    return clusterer.fit_predict(embeddings).tolist()


LABEL_PROMPT = """You name narrative clusters found in coverage of a Kenyan politician. Posts may be in English, Swahili or Sheng; ALWAYS write the label and description in English.

You are given numbered clusters, each with sample posts. For every cluster return:
  - "label": 2-5 words naming the ACTUAL STORY, as a newsroom would headline it.
    Good: "Kitale mega rally", "TIFA poll surge", "Clash with Ruto".
    Bad: "Cluster 3", "Political posts", "Mixed sentiment", "Social media".
    Never number a label. Never describe the medium instead of the story.
  - "description": one sentence saying what is being claimed or reported and by whom.

The required JSON shape is:
{"clusters": [{"id": <the cluster id given to you>, "label": "...", "description": "..."}]}
Return one entry for EVERY cluster id you were given."""

# Words that carry no topical signal in Kenyan political coverage, so a derived
# label built from them would name nothing. Includes the boilerplate that
# YouTube descriptions and news footers repeat on every single item.
_STOPWORDS = frozenset("""
a an the and or but if of to in on at by for with from as is are was were be been being
this that these those it its his her their our your my we you they he she i not no nor so
do does did doing done have has had having would could should might must may shall
into onto upon amid amid while whose been being does
than then there here when what which who whom how why all any both each few more most other
some such only own same too very can will just should now about after before over under
video news kenya kenyan live watch subscribe channel latest today daily update updates
comment comments like share follow following click link bio join access perks get out up
say says said new one two three ke com www https http tv show shows full part episode
county senator mp mca hon president governor political politics
""".split())

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}")

#: Suffixes trimmed before two words are treated as the same word, longest
#: first.
#:
#: Matching on surface forms fails on the pair it is most often asked
#: about: "approval delays" and "waiting on approvals" are the same story
#: and share no token at all. A synonym list would have had to be taught
#: that one case; folding the inflection fixes the category. It also stops
#: a derived label reading "Approval Approvals Chronic", which is one
#: concept spelled twice and a wasted slot out of three.
_SUFFIXES = (("ies", "y"), ("ing", ""), ("edly", ""), ("ed", ""),
             ("es", ""), ("ly", ""), ("s", ""))

#: A stem shorter than this is not evidence of anything.
_MIN_STEM = 4


def stem(word: str) -> str:
    """Crude, deliberate and explainable. Not a linguistic stemmer: enough
    to stop one concept counting twice, and small enough that anyone
    reading a surprising grouping can see why it happened."""
    for suffix, replacement in _SUFFIXES:
        trimmed = len(word) - len(suffix) + len(replacement)
        if word.endswith(suffix) and trimmed >= _MIN_STEM:
            return word[: -len(suffix)] + replacement
    return word


#: Where a one-line name is allowed to end.
_LABEL_CHARS = 64


def short_label(text: str, limit: int = _LABEL_CHARS) -> str:
    """A headline's opening, cut at a word boundary.

    Used where a name is needed for a single item, which word-frequency
    cannot supply. Cut at a clause first — a comma or a dash is the
    writer's own mark for where the point ends — and at a word otherwise.
    """
    body = " ".join((text or "").split())
    if not body:
        return "Unlabelled coverage"
    for mark in (" — ", " – ", ", ", "; ", ": "):
        head = body.split(mark)[0]
        if 16 <= len(head) <= limit:
            return head
    if len(body) <= limit:
        return body
    cut = body[:limit].rsplit(" ", 1)[0]
    return (cut or body[:limit]).rstrip(",;:-") + "…"


def derived_label(texts: list[str], exclude: set[str] | None = None) -> tuple[str, str]:
    """Name a cluster from its own words, with no model call.

    This is the floor, not a nicety. When labelling fails — a rate limit, a
    provider outage, a model that returns prose — the previous fallback emitted
    "narrative-3", which tells a reader nothing and cannot be acted on. A label
    built from the cluster's most distinctive terms is always at least a
    description of what the posts are about.

    Terms are scored by how many DISTINCT posts contain them, not raw frequency,
    so one 400-word article cannot name the whole cluster on its own.
    """
    exclude = {w.lower() for w in (exclude or set())}
    # Counted by STEM, shown in whichever surface form the posts use most.
    # Counting surface forms gave labels like "Approval Approvals Chronic":
    # one concept spelled twice, and a third of a three-word name wasted on
    # the duplicate.
    doc_freq: dict[str, int] = {}
    surface: dict[str, dict[str, int]] = {}
    for text in texts:
        seen = set()
        for match in _WORD_RE.findall(text or ""):
            word = match.lower()
            if word in _STOPWORDS or word in exclude or len(word) < 4:
                continue
            root = stem(word)
            seen.add(root)
            forms = surface.setdefault(root, {})
            forms[word] = forms.get(word, 0) + 1
        for root in seen:
            doc_freq[root] = doc_freq.get(root, 0) + 1

    if not doc_freq:
        return "Unlabelled coverage", ""

    # One document has no document frequencies worth the name — every word
    # in it occurs in exactly one of one, so "ranking" them is alphabetical
    # order wearing a statistic's clothes. It produced "Authority Cover
    # Does" for a headline about registration passing 4.2 million members.
    # A single item is named by what it says.
    if len(texts) == 1:
        return short_label(texts[0]), (
            "A single item, named from its own opening rather than from word "
            "frequency, which over one document measures nothing.")
    # Only words the cluster actually shares, when it shares any.
    #
    # Ranking by document frequency and taking three is right for a cluster
    # of twenty and wrong for a cluster of two, where one word appears in
    # both and every other word appears in exactly one — so the second and
    # third slots are filled alphabetically and the label reads "Approval
    # Chronic Dates", two thirds of which name nothing. A short true label
    # beats a long arbitrary one; the strongest item's own headline is
    # shown underneath it either way.
    shared = {root: n for root, n in doc_freq.items() if n > 1}
    pool = shared or doc_freq
    ranked = [
        (max(surface[root].items(), key=lambda kv: (kv[1], -len(kv[0])))[0], count)
        for root, count in sorted(pool.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    ]
    label = " ".join(word.capitalize() for word, _ in ranked)
    description = (
        f"Recurring coverage around {', '.join(w for w, _ in ranked)} "
        f"across {len(texts)} mentions. Named from the text of the cluster itself, "
        "because automatic labelling did not return a name for it."
    )
    return label, description


def _looks_useless(label: str) -> bool:
    """Reject a label that names nothing — including the numbered placeholders
    a struggling model reaches for."""
    if not label or not label.strip():
        return True
    cleaned = label.strip().lower()
    if re.fullmatch(r"(narrative|cluster|topic|theme|group)[\s._-]*\d*", cleaned):
        return True
    return cleaned in {"unknown", "n/a", "none", "other", "misc", "miscellaneous", "general"}


def label_cluster(sample_texts: list[str]) -> dict:
    """Label one cluster. Kept for callers that label a single cluster."""
    labelled = label_clusters([(0, sample_texts)])
    return labelled.get(0, {})


#: Total wall-clock budget for labelling EVERY cluster, however many calls
#: that ends up taking. Each individual call is already bounded by llm.py's
#: own retry budget (up to 240s, shrinking as failures accumulate) — but on
#: failure this function recurses, splitting the batch in half and retrying
#: each half, and NOTHING previously bounded the total depth of that tree. A
#: flaky free model that fails four times then succeeds once resets the
#: circuit breaker on every success, so the breaker never opens and every one
#: of dozens of clusters can pay its own full multi-minute retry budget in
#: turn — the run sits on "Narratives" for as long as the model keeps being
#: just reliable enough to avoid tripping the breaker. Every cluster already
#: has a derived, keyword-based fallback label (`derived_label`, used by
#: `build_narratives` for anything this returns without an entry) — a report
#: with derived labels beats a report that never advances.
LABEL_CLUSTERS_DEADLINE_SECONDS = 150.0


def label_clusters(clusters: list[tuple[int, list[str]]], _depth: int = 0,
                   _deadline: float | None = None) -> dict[int, dict]:
    """Label every cluster in ONE call, splitting the batch when a call fails.

    Previously each cluster got its own concurrent call — up to two dozen at
    once. A provider rate limit then failed all of them together and every
    narrative in the report came out as "narrative-N". One request for the whole
    set is both cheaper and far less likely to be throttled, and a failure that
    does happen degrades a half, not the report.
    """
    if not clusters:
        return {}

    # Set once, at the top of the tree, and threaded through every recursive
    # call so the WHOLE tree shares one clock rather than each split getting
    # its own fresh budget.
    if _deadline is None:
        _deadline = time.monotonic() + LABEL_CLUSTERS_DEADLINE_SECONDS
    elif time.monotonic() > _deadline:
        # Out of time, not out of clusters. Stop asking the model and hand
        # everything remaining back unlabelled — build_narratives derives a
        # label from each cluster's own text instead.
        stages.current().failed(
            f"narrative_labelling[{len(clusters)}]",
            f"labelling stage exceeded its {LABEL_CLUSTERS_DEADLINE_SECONDS:g}s budget; "
            f"{len(clusters)} cluster(s) will get a derived label instead of a model one")
        return {}

    blocks = []
    for cluster_id, texts in clusters:
        samples = "\n".join(f"  - {(t or '')[:300]}" for t in texts[:6])
        blocks.append(f"Cluster {cluster_id} ({len(texts)} posts):\n{samples}")
    user = "\n\n".join(blocks)

    try:
        reply = llm.call_json_untrusted(
            LABEL_PROMPT, user, expected_keys={"clusters"},
            max_tokens=llm.budget_for(400 * len(clusters) + 600),
        )
        entries = reply.get("clusters") or []
        if not isinstance(entries, list):
            raise ValueError("clusters was not a list")
    except Exception as exc:  # noqa: BLE001
        stages.current().failed(f"narrative_labelling[{len(clusters)}]", exc)
        if len(clusters) > 1:
            middle = len(clusters) // 2
            return {
                **label_clusters(clusters[:middle], _depth + 1, _deadline),
                **label_clusters(clusters[middle:], _depth + 1, _deadline),
            }
        return {}

    valid_ids = {cid for cid, _ in clusters}
    out: dict[int, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            cluster_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if cluster_id not in valid_ids:
            continue
        label = str(entry.get("label") or "").strip()
        if _looks_useless(label):
            continue
        out[cluster_id] = {"label": label[:80],
                           "description": str(entry.get("description") or "").strip()[:400]}
    return out


def _evidence_for(cluster_mentions: list[dict], limit: int = 8) -> list[dict]:
    """The receipts for a narrative: the mentions a reader can go and check.

    A narrative that cannot be opened is an assertion. Carrying the actual
    posts — with their URL, author, date and engagement — is what turns it into
    a finding, and it is the difference between "trust me" and "here, look".
    """
    def weight(mention: dict) -> int:
        eng = mention.get("engagement") or {}
        return sum(int(eng.get(k, 0) or 0) for k in ("views", "likes", "shares", "comments"))

    ranked = sorted(cluster_mentions, key=weight, reverse=True)[:limit]
    evidence = []
    for mention in ranked:
        text = (mention.get("text") or "").strip()
        evidence.append({
            "mention_id": mention.get("id"),
            "platform": mention.get("platform"),
            "author": mention.get("author_handle"),
            "url": mention.get("source_url"),
            "posted_at": mention.get("posted_at").isoformat()
            if hasattr(mention.get("posted_at"), "isoformat") else mention.get("posted_at"),
            "engagement": weight(mention),
            "excerpt": text[:400],
        })
    return evidence


def build_narratives(mentions: list[dict], subject_terms: set[str] | None = None) -> list[dict]:
    """Groups mentions into narrative clusters, labels each via the LLM,
    and computes strength/growth metrics per cluster.

    `mentions` items need: id, text, posted_at, engagement (dict with likes/shares/comments).
    """
    if not mentions:
        return []

    # The subject's own name is in nearly every mention, so it distinguishes
    # nothing and must not become the derived label of every cluster.
    subject_terms = subject_terms or set()

    texts = [m["text"] for m in mentions]
    labels = cluster_mentions(texts)

    clusters: dict[int, list[dict]] = defaultdict(list)
    for mention, label in zip(mentions, labels):
        if label == -1:
            continue
        clusters[label].append(mention)

    # Largest clusters first: if labelling degrades, the narratives that carry
    # the most of the corpus are the ones that got a real name.
    cluster_items = sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True)
    labelled = label_clusters([(cid, [m["text"] for m in items]) for cid, items in cluster_items])

    results = []
    for cluster_id, cluster_mentions_ in cluster_items:
        meta = labelled.get(cluster_id) or {}
        if not meta.get("label"):
            # No model label. Name it from its own text rather than by number —
            # "narrative-3" is unreadable and unusable, and it was what every
            # narrative in a rate-limited run came out as.
            fallback_label, fallback_description = derived_label(
                [m["text"] for m in cluster_mentions_], exclude=subject_terms
            )
            meta = {"label": fallback_label, "description": fallback_description,
                    "labelled_by": "derived"}
        else:
            meta = {**meta, "labelled_by": "model"}
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
                "label": meta["label"],
                "description": meta.get("description", ""),
                "labelled_by": meta.get("labelled_by", "derived"),
                "mention_ids": [m["id"] for m in cluster_mentions_],
                # The receipts travel WITH the narrative all the way to the
                # page, so a reader can open it instead of taking it on faith.
                "evidence": _evidence_for(cluster_mentions_),
                "strength_score": strength_score,
                "growth_rate": growth_rate,
                "window_start": min(timestamps),
                "window_end": max(timestamps),
            }
        )
    return results
