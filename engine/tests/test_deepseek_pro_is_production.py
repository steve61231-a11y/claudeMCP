"""DeepSeek's strongest model is production; each tier is priced at its own rate.

The founder's decision of 2 Nov 2026: production runs on DeepSeek's
strongest model through OpenRouter, not Anthropic. A report from it must
not carry the "Test-grade" stamp; a cheap Flash model as the STRONG tier
still does. And with DeepSeek V4 Pro's output at ~8x V4.1 Flash's, one
price for both tiers misstates the bill — the bulk tier's share of the
tokens is recorded and priced at its own rate.
"""

from engine import llm
from engine.config import settings


def _set(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setattr(settings, k, v)


def test_the_strongest_deepseek_model_is_production(monkeypatch):
    _set(monkeypatch, llm_provider="openai_compatible", llm_model="deepseek/deepseek-v4-pro-0813",
         llm_bulk_model="deepseek/deepseek-v4.1-flash")
    assert not llm.is_test_grade()
    grade = llm.report_grade()
    assert grade["production"] and "warning" not in grade


def test_a_flash_model_as_the_strong_tier_is_still_test_grade(monkeypatch):
    _set(monkeypatch, llm_provider="openai_compatible", llm_model="deepseek/deepseek-v4.1-flash")
    assert llm.is_test_grade() and llm.report_grade()["warning"].startswith("Test-grade")


def test_anthropic_is_still_production_and_the_stub_never_is(monkeypatch):
    _set(monkeypatch, llm_provider="anthropic")
    assert not llm.is_test_grade()
    _set(monkeypatch, llm_provider="stub")
    assert llm.is_test_grade()


def test_each_tier_is_recorded_at_its_own_rate(monkeypatch):
    _set(monkeypatch, llm_provider="openai_compatible", llm_model="deepseek/deepseek-v4-pro-0813",
         llm_bulk_model="deepseek/deepseek-v4.1-flash")
    seen = []
    monkeypatch.setattr(llm, "_persist_usage", lambda i, o, bulk=False: seen.append((i, o, bulk)))
    llm._record_openai_usage({"model": "deepseek/deepseek-v4.1-flash", "usage": {"prompt_tokens": 10,
                                                                                "completion_tokens": 2}})
    llm._record_openai_usage({"model": "deepseek/deepseek-v4-pro-0813", "usage": {"prompt_tokens": 7,
                                                                                 "completion_tokens": 3}})
    assert seen == [(10, 2, True), (7, 3, False)]
