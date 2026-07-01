from engine.db.models import Politician
from engine.ingestion import queries


def make_politician(**overrides):
    defaults = dict(
        name="John Mbadi",
        aliases=["Mbadi"],
        keywords=["Treasury", "Suba South"],
        titles=["CS", "Waziri wa Fedha"],
        swahili_terms=["waziri wa pesa"],
        tracked_hashtags=["#Mbadi", "FinanceBill"],
    )
    defaults.update(overrides)
    return Politician(**defaults)


def test_text_variants_include_name_aliases_titles_and_swahili():
    variants = queries.text_variants(make_politician())
    assert "John Mbadi" in variants
    assert "Mbadi" in variants
    assert "CS Mbadi" in variants  # title + surname
    assert "Waziri wa Fedha" in variants  # multi-word title stands alone
    assert "Waziri wa Fedha Mbadi" in variants
    assert "Hon Mbadi" in variants
    assert "waziri wa pesa" in variants


def test_text_variants_deduped_and_capped():
    politician = make_politician(aliases=["Mbadi", "mbadi", "MBADI "])
    variants = queries.text_variants(politician)
    assert len([v for v in variants if v.lower() == "mbadi"]) == 1
    assert len(variants) <= queries.MAX_TEXT_VARIANTS


def test_hashtag_variants_strip_hash_and_add_name_forms():
    variants = queries.hashtag_variants(make_politician())
    assert "Mbadi" in variants
    assert "FinanceBill" in variants
    assert "JohnMbadi" in variants
    assert all(not v.startswith("#") for v in variants)


def test_detect_language_swahili():
    assert queries.detect_language("Waziri amesema hakuna pesa ya kutosha kwa wananchi sasa") == "sw"


def test_detect_language_english():
    assert queries.detect_language("The Treasury CS announced new budget measures today") == "en"


def test_detect_language_empty():
    assert queries.detect_language("") == "und"
