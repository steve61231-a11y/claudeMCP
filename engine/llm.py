import json

from anthropic import Anthropic

from engine.config import settings

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def call_json(prompt: str, max_tokens: int = 1024) -> dict | list:
    """Calls Claude and parses a JSON object/array from the response text."""
    response = get_client().messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])
