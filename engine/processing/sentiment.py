from engine import llm, stages
from engine.config import settings

_local_pipeline = None
_local_pipeline_unavailable = False


def get_local_pipeline():
    global _local_pipeline, _local_pipeline_unavailable
    if _local_pipeline_unavailable:
        raise RuntimeError("Local sentiment model previously failed to load")
    if not settings.use_local_ml:
        # Never import torch/transformers on a memory-constrained deploy — the
        # LLM sentiment path is used instead (see analyze_sentiment).
        _local_pipeline_unavailable = True
        raise RuntimeError("local ML disabled (USE_LOCAL_ML=false)")
    if _local_pipeline is None:
        from transformers import pipeline

        try:
            _local_pipeline = pipeline(
                "sentiment-analysis", model="distilbert-base-uncased-finetuned-sst-2-english"
            )
        except Exception:
            _local_pipeline_unavailable = True
            raise
    return _local_pipeline


def local_sentiment(text: str) -> dict:
    """Cheap, high-volume sentiment + confidence via a local transformer model."""
    result = get_local_pipeline()(text[:512])[0]
    label = "positive" if result["label"] == "POSITIVE" else "negative"
    confidence = float(result["score"])
    intensity = max(1, min(5, round(confidence * 5)))
    return {"sentiment": label, "confidence": confidence, "intensity": intensity}


def local_sentiment_available() -> bool:
    """Probes whether the local model can actually be loaded, without raising.

    Deployments without network access to HuggingFace Hub (or that simply
    haven't pre-cached the model) shouldn't crash the pipeline — they should
    fall back to the LLM for every mention instead of just low-confidence ones.
    """
    try:
        get_local_pipeline()
        return True
    except Exception:
        return False


CONTEXT_PROMPT = """Classify the political tone and intensity of this social media text about a politician. The text may be in English, Swahili, or Sheng — classify it in whatever language it is written.

The required JSON shape is:
{"sentiment": "positive"|"neutral"|"negative", "intensity": 1-5, "context_tag": "support"|"attack"|"concern"|"praise"}"""


def llm_sentiment_and_context(text: str) -> dict:
    """LLM pass for context tagging, and for any mention the local model is unsure about."""
    try:
        result = llm.call_json_untrusted(CONTEXT_PROMPT, text, expected_keys={"sentiment", "intensity"}, max_tokens=200)
    except Exception as exc:  # noqa: BLE001
        # A failed call must not become a scored "neutral". Neutral is a
        # reading; an unanswered request is not, and counting one as the other
        # inflates the neutral share with data that was never produced. Only
        # ValueError was caught here, so a provider RuntimeError escaped too.
        stages.current().failed("llm_sentiment", exc)
        return {"sentiment": None, "intensity": None, "context_tag": None, "scored": False}
    return {
        "scored": True,
        "sentiment": result.get("sentiment", "neutral"),
        "intensity": int(result.get("intensity", 3)),
        "context_tag": result.get("context_tag"),
        "confidence": 0.9,
    }


def analyze_sentiment(text: str) -> dict:
    """Local model for the bulk; escalate low-confidence mentions and always
    use the LLM for the context_tag, since that's a nuance local models don't capture.
    Falls back to LLM-only when the local model can't be loaded at all (e.g. no
    network access to HuggingFace Hub in this deployment).
    """
    if not local_sentiment_available():
        llm_result = llm_sentiment_and_context(text)
        return {**llm_result, "source": "llm"}

    local_result = local_sentiment(text)
    if local_result["confidence"] < settings.sentiment_confidence_threshold:
        llm_result = llm_sentiment_and_context(text)
        return {**llm_result, "source": "llm"}

    llm_context = llm_sentiment_and_context(text)
    return {
        "sentiment": local_result["sentiment"],
        "intensity": local_result["intensity"],
        "context_tag": llm_context.get("context_tag"),
        "confidence": local_result["confidence"],
        "source": "local",
    }

# ---------------------------------------------------------------------------
# Lexicon fallback: a sentiment reading that costs no model call at all.
#
# Every path above — local_sentiment (a downloaded transformer) and
# llm_sentiment_and_context (a model call) — can fail or be unavailable at
# once: no network route to HuggingFace Hub, and a free LLM backend refusing
# every request. When that happens the correct, honest thing was already
# built here — an unanswered call returns `scored: False`, never a fabricated
# "neutral" — but the CONSEQUENCE was that Positive/Negative rendered as "—"
# for the whole corpus, on every mention, with nothing behind that honesty.
# derived_label() already does this for narratives (a keyword-derived name
# instead of "narrative-3"); this is the same idea for sentiment: weak,
# clearly labelled, and never confused with a model's reading.
# ---------------------------------------------------------------------------

_POSITIVE_WORDS = frozenset("""
good great excellent success successful win wins won winning praise praised
progress improve improved improving support supports supported supportive
achievement achievements deliver delivers delivered delivering strong
welcome welcomed applaud applauds impressive commend commends thank thanks
grateful proud pride hope hopeful optimism optimistic breakthrough milestone
victory triumph endorse endorses endorsed backing celebrate celebrated
poa nzuri vizuri safi sawa
""".split())

_NEGATIVE_WORDS = frozenset("""
bad terrible fail fails failed failure failing corrupt corruption scandal
crisis condemn condemns condemned condemnation criticize criticizes
criticized criticism attack attacks attacked outrage outraged anger angry
protest protests protested protesting resign resigns resigned resignation
arrest arrests arrested arraigned charged accuse accuses accused accusation
disaster disastrous collapse collapsed betray betrayed betrayal shame
shameful reject rejects rejected rejection blame blamed blames worst
scandalous embezzle embezzlement fraud fraudulent lie lies lied lying
mbaya wizi ufisadi uongo aibu
""".split())

_WORD_SPLIT_RE = None


def lexicon_sentiment(text: str) -> dict:
    """A sentiment reading with no model and no network call.

    Deliberately crude — counting matched words, not understanding the text —
    and deliberately never claims otherwise: `confidence` is fixed low and
    `source` is always "lexicon", so nothing downstream can mistake this for a
    model's judgement of tone. It exists because a reading this weak still
    beats the alternative, which was every mention rendering as unscored the
    moment the local model AND the LLM were both unavailable in the same run.
    """
    import re as _re

    global _WORD_SPLIT_RE
    if _WORD_SPLIT_RE is None:
        _WORD_SPLIT_RE = _re.compile(r"[a-zA-Z']+")

    words = [w.lower() for w in _WORD_SPLIT_RE.findall(text or "")]
    positive = sum(1 for w in words if w in _POSITIVE_WORDS)
    negative = sum(1 for w in words if w in _NEGATIVE_WORDS)

    if positive == 0 and negative == 0:
        sentiment = "neutral"
        intensity = 1
    elif positive > negative:
        sentiment = "positive"
        intensity = max(1, min(5, 1 + positive - negative))
    elif negative > positive:
        sentiment = "negative"
        intensity = max(1, min(5, 1 + negative - positive))
    else:
        sentiment = "neutral"
        intensity = 2

    return {
        "sentiment": sentiment,
        "intensity": intensity,
        "context_tag": None,
        # Fixed and low on purpose: this must never look more certain than a
        # model's own low-confidence threshold (sentiment_confidence_threshold,
        # 0.55 by default) so nothing treats it as a real analyst reading.
        "confidence": 0.2,
        "source": "lexicon",
    }
