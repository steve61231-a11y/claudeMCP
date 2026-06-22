from engine import llm
from engine.config import settings

_local_pipeline = None


def get_local_pipeline():
    global _local_pipeline
    if _local_pipeline is None:
        from transformers import pipeline

        _local_pipeline = pipeline(
            "sentiment-analysis", model="distilbert-base-uncased-finetuned-sst-2-english"
        )
    return _local_pipeline


def local_sentiment(text: str) -> dict:
    """Cheap, high-volume sentiment + confidence via a local transformer model."""
    result = get_local_pipeline()(text[:512])[0]
    label = "positive" if result["label"] == "POSITIVE" else "negative"
    confidence = float(result["score"])
    intensity = max(1, min(5, round(confidence * 5)))
    return {"sentiment": label, "confidence": confidence, "intensity": intensity}


CONTEXT_PROMPT = """Classify the political tone and intensity of this social media text about a politician.

Text: "{text}"

Respond with ONLY a JSON object:
{{"sentiment": "positive"|"neutral"|"negative", "intensity": 1-5, "context_tag": "support"|"attack"|"concern"|"praise"}}
"""


def llm_sentiment_and_context(text: str) -> dict:
    """LLM pass for context tagging, and for any mention the local model is unsure about."""
    result = llm.call_json(CONTEXT_PROMPT.format(text=text), max_tokens=200)
    return {
        "sentiment": result.get("sentiment", "neutral"),
        "intensity": int(result.get("intensity", 3)),
        "context_tag": result.get("context_tag"),
        "confidence": 0.9,
    }


def analyze_sentiment(text: str) -> dict:
    """Local model for the bulk; escalate low-confidence mentions and always
    use the LLM for the context_tag, since that's a nuance local models don't capture.
    """
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
