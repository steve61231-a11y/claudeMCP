"""Depth guard rails.

The reports were shallow for reasons that were all in our own code, and every
one of them is the kind of thing that silently comes back:

- an analyst prompt whose schema example shows one element per array, because
  models copy the example's cardinality,
- an output budget small enough that the cap, not the evidence, decides how
  much gets written,
- a payload field the API emits that no renderer reads, so the analysis is
  generated, paid for and dropped on the floor.

These tests fail when any of those regress.
"""

import re
from pathlib import Path

import pytest

from engine import llm
from engine.config import settings
from engine.reports import analysts, digest, sections

REPO = Path(__file__).resolve().parents[2]
APP_HTML = REPO / "web" / "pulse_app.html"
API_SERVER = REPO / "engine" / "api_server.py"


# --------------------------------------------------------------------------
# Prompts must not demonstrate thinness
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "prompt",
    [
        analysts.ISSUE_MAP_PROMPT,
        analysts.PUBLIC_VOICE_PROMPT,
        analysts.PLATFORM_PULSE_PROMPT,
        analysts.TIMELINE_PROMPT,
        analysts.INFLUENCER_STANCES_PROMPT,
        analysts.NARRATIVE_DEEP_DIVE_PROMPT,
        digest.MAP_PROMPT,
    ],
)
def test_schema_examples_are_multi_element(prompt: str):
    """A one-element example is an instruction to return one element.

    Every prompt that asks for a list must either show several elements or say
    outright that the example's length is not the target.
    """
    shows_several = re.search(r"\}\}\s*,\s*\{\{", prompt) is not None
    disclaims_quantity = "not the QUANTITY" in prompt
    assert shows_several or disclaims_quantity, (
        "schema example demonstrates a single-element array and never says the "
        "length is illustrative — models will mirror it"
    )


@pytest.mark.parametrize(
    "prompt",
    [
        analysts.ISSUE_MAP_PROMPT,
        analysts.TIMELINE_PROMPT,
        analysts.PUBLIC_VOICE_PROMPT,
    ],
)
def test_prose_prompts_state_a_length(prompt: str):
    """Without a length, an analyst writes a headline fragment."""
    assert re.search(r"\d+-\d+ words", prompt), "no per-field length guidance"


def test_issue_map_prompt_asks_for_every_actor():
    """3-4 actors was the symptom; the floor is the fix."""
    assert "15-40" in analysts.ISSUE_MAP_PROMPT
    assert "80-200 words" in analysts.ISSUE_MAP_PROMPT


def test_map_step_forbids_omission():
    """Downstream never sees the mentions again — only this digest."""
    assert "Omission is not" in digest.MAP_PROMPT


# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------

def test_analyst_budgets_are_not_the_binding_constraint():
    assert analysts.ANALYST_MAX_TOKENS >= 8000
    assert sections.SECTION_MAX_TOKENS >= 2000
    assert digest.MAP_MAX_TOKENS >= 4000
    assert analysts.DIGEST_CONTEXT_CHARS >= 80000


def test_output_ceiling_follows_the_backend(monkeypatch):
    """The OpenAI-compatible path is bound by DeepSeek's 8192. Claude is not,
    and the paid backend must not inherit a stand-in's limit."""
    monkeypatch.setattr(settings, "llm_max_output_tokens", 0, raising=False)

    monkeypatch.setattr(settings, "llm_provider", "openai_compatible", raising=False)
    assert llm.max_output_tokens() == llm.OPENAI_COMPATIBLE_MAX_TOKENS

    monkeypatch.setattr(settings, "llm_provider", "anthropic", raising=False)
    assert llm.max_output_tokens() == llm.ANTHROPIC_MAX_OUTPUT_TOKENS
    assert llm.max_output_tokens() > llm.OPENAI_COMPATIBLE_MAX_TOKENS

    monkeypatch.setattr(settings, "llm_max_output_tokens", 4321, raising=False)
    assert llm.max_output_tokens() == 4321


# --------------------------------------------------------------------------
# Nothing generated may be silently discarded
# --------------------------------------------------------------------------

def _emitted_report_keys() -> set[str]:
    """Top-level camelCase keys of the report payload the API returns."""
    source = API_SERVER.read_text(encoding="utf-8")
    block = source[source.index('"freshness": _freshness('):]
    block = block[: block.index('\n    }\n')]
    return {
        m.group(1)
        for m in re.finditer(r'^        "([A-Za-z]+)":', block, re.M)
    }


def test_every_emitted_section_has_a_renderer():
    """`publicVoice`, `platformPulse`, `timeline`, `influencerStances` and
    `narrativeDeepDives` were generated on every run and read by nothing. The
    richest output in the system was being thrown away in the browser."""
    html = APP_HTML.read_text(encoding="utf-8")
    emitted = _emitted_report_keys()
    assert "publicVoice" in emitted, "payload shape changed — update this test"
    unread = sorted(k for k in emitted if f"r.{k}" not in html and f".{k}" not in html)
    assert not unread, f"API emits sections nothing renders: {unread}"


def test_issue_map_intersection_reaches_the_screen():
    """The framework view is a presentation of the intersection, not a
    replacement for it — rendering only the framework hid the analysis."""
    html = APP_HTML.read_text(encoding="utf-8")
    assert "function intersectionDetail(" in html
    for field in ("linking_narratives", "key_actors", "tension_or_risk", "involvement"):
        assert f"it.{field}" in html, f"intersection.{field} is never rendered"
