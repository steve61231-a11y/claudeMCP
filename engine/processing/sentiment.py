from engine import llm
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
    except ValueError:
        result = {}
    return {
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
