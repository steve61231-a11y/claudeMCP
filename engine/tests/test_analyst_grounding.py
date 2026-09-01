"""Why sections came back blank on runs where the model worked fine.

Two mechanisms, both silent, both total-loss:

  1. Quote validation matched raw bytes. A model that straightens a curly
     apostrophe or marks an elision with "…" is quoting faithfully; the matcher
     called it fabricated. Public voice then DELETED the whole theme — its
     80-150 word analysis included — because its illustration failed.

  2. Full-article enrichment made each item 6000 characters, so the 60k analyst
     window held nine items out of 661. The analyst answered "what are people
     saying" from 1.4% of the corpus, faithfully.
"""

from datetime import datetime

from engine.reports import analysts as an

SOURCE = ("Sifuna’s remarks didn’t go down well — “We won’t accept it,” "
          "he told the crowd at Kitale, adding that the coalition would decide in January.")


# --- quote grounding ---------------------------------------------------------

def test_a_verbatim_quote_is_grounded():
    assert an.quote_is_grounded("Sifuna’s remarks didn’t go down well", SOURCE)


def test_straightened_smart_quotes_are_still_the_same_quote():
    assert an.quote_is_grounded('"We won\'t accept it," he told the crowd at Kitale', SOURCE)


def test_a_flattened_em_dash_is_still_the_same_quote():
    assert an.quote_is_grounded("go down well - “We won’t accept it,”", SOURCE)


def test_an_elision_marked_with_an_ellipsis_is_still_grounded():
    assert an.quote_is_grounded(
        "We won’t accept it … the coalition would decide in January", SOURCE)


def test_added_trailing_punctuation_does_not_sink_a_quote():
    assert an.quote_is_grounded("Sifuna’s remarks didn’t go down well.", SOURCE)


def test_a_paraphrase_is_not_grounded():
    assert not an.quote_is_grounded(
        "Sifuna angrily rejected the proposal at a rally", SOURCE)


def test_a_quote_from_a_different_story_is_not_grounded():
    assert not an.quote_is_grounded("The finance bill was rejected in Machakos", SOURCE)


def test_a_short_invented_phrase_is_not_grounded():
    assert not an.quote_is_grounded("total betrayal", SOURCE)


def test_validation_still_requires_the_ref_to_exist():
    refs = {"aaaaaaaa": {"text": SOURCE, "author_handle": "a"}}
    kept = an._validate_quotes(
        [{"ref": "zzzzzzzz", "text": "Sifuna’s remarks didn’t go down well"}], refs)
    assert kept == [], "a quote attributed to a mention that does not exist is dropped"


def test_validation_rejects_a_real_quote_attributed_to_the_wrong_mention():
    refs = {"aaaaaaaa": {"text": "Something else entirely about a budget.",
                         "author_handle": "a"}}
    kept = an._validate_quotes(
        [{"ref": "aaaaaaaa", "text": "We won’t accept it"}], refs)
    assert kept == []


# --- the theme must survive its quotes ---------------------------------------

def _voice_result(quotes):
    """Public voice now runs ONE CALL PER STANCE — three smaller replies instead
    of one that asked for 3,000+ words and was cut off. Each returns {"themes":
    [...]} for the stance it was asked about."""
    return {"themes": [{"theme": "Defends the coalition",
                        "summary": "A long analytical summary of what supporters say.",
                        "quotes": list(quotes)}]}


def test_a_theme_is_kept_when_its_quotes_cannot_be_verified(monkeypatch):
    """The theme is the analysis; the quotes illustrate it. Deleting the finding
    because its illustration failed is what emptied whole sections."""
    monkeypatch.setattr(an.llm, "call_json_untrusted",
                        lambda *a, **k: _voice_result([{"ref": "zzzzzzzz", "text": "invented"}]))
    voice = an.analyze_public_voice("Sifuna", [
        {"id": "aaaaaaaa", "text": SOURCE, "platform": "x", "source_type": "post",
         "author_handle": "a", "posted_at": datetime(2026, 7, 1), "engagement": {}}])
    assert len(voice["supportive"]) == 1
    assert voice["supportive"][0]["summary"]
    assert voice["supportive"][0]["quotes"] == []
    assert voice["supportive"][0]["quotes_unverified"] is True, "say so, don't hide it"


def test_a_theme_with_no_analysis_at_all_is_still_dropped(monkeypatch):
    monkeypatch.setattr(an.llm, "call_json_untrusted",
                        lambda *a, **k: {"themes": [{"quotes": []}]})
    voice = an.analyze_public_voice("Sifuna", [])
    assert voice["supportive"] == []


def test_a_verified_quote_survives_end_to_end(monkeypatch):
    monkeypatch.setattr(an.llm, "call_json_untrusted", lambda *a, **k: _voice_result(
        [{"ref": "aaaaaaaa", "text": '"We won\'t accept it," he told the crowd at Kitale'}]))
    voice = an.analyze_public_voice("Sifuna", [
        {"id": "aaaaaaaa", "text": SOURCE, "platform": "x", "source_type": "post",
         "author_handle": "nation", "posted_at": datetime(2026, 7, 1), "engagement": {}}])
    quote = voice["supportive"][0]["quotes"][0]
    assert quote["ref"] == "aaaaaaaa"
    assert quote["author"] == "nation"
    assert "quotes_unverified" not in voice["supportive"][0]


# --- corpus breadth ----------------------------------------------------------

def _corpus(n, chars, source_type="article"):
    return [{"id": f"id{i:06d}", "platform": "nation.africa", "source_type": source_type,
             "author_handle": "nation", "text": "word " * (chars // 5),
             "posted_at": datetime(2026, 7, 1), "engagement": {"views": i}}
            for i in range(n)]


def test_enriched_articles_no_longer_crowd_the_window_down_to_nine(monkeypatch):
    """Nine of 661 was the defect. The exact number depends on the configured
    window, which is now tunable per deployment — what must hold is that a
    full-article corpus is read in the hundreds, not single figures."""
    from engine.config import settings

    monkeypatch.setattr(settings, "analyst_corpus_chars", 100000, raising=False)
    an._corpus_blob(_corpus(661, 6000))
    read = an.corpus_window_stats()["read"]
    assert read > 100, f"only {read} of 661 mentions reached the analyst"


def test_a_wider_window_reads_more_of_the_corpus(monkeypatch):
    """The setting has to actually do something."""
    from engine.config import settings

    monkeypatch.setattr(settings, "analyst_corpus_chars", 40000, raising=False)
    an._corpus_blob(_corpus(661, 6000))
    narrow = an.corpus_window_stats()["read"]
    monkeypatch.setattr(settings, "analyst_corpus_chars", 160000, raising=False)
    an._corpus_blob(_corpus(661, 6000))
    wide = an.corpus_window_stats()["read"]
    assert wide > narrow * 2


def test_short_social_items_are_read_almost_entirely(monkeypatch):
    from engine.config import settings

    monkeypatch.setattr(settings, "analyst_corpus_chars", 100000, raising=False)
    an._corpus_blob(_corpus(661, 120, source_type="comment"))
    stats = an.corpus_window_stats()
    assert stats["read"] > 500, (
        f"only {stats['read']} of 661 short posts reached the analyst — the "
        "per-line ref/platform/date header is half the payload on social items")


def test_the_default_window_is_not_so_wide_a_small_model_chokes():
    """160k characters is ~40k tokens. A free-tier model with a small context
    or a hard rate cap either refuses that or queues it until something
    upstream gives up waiting — which is how a report hung forever."""
    from engine.config import Settings

    assert 40000 <= Settings().analyst_corpus_chars <= 120000


def test_a_long_article_is_truncated_but_a_short_post_is_not():
    long_article = an._render_mention(
        {"id": "a", "source_type": "article", "text": "word " * 4000,
         "platform": "p", "author_handle": "h", "posted_at": datetime(2026, 7, 1),
         "engagement": {}})
    assert len(long_article) < 1200 and long_article.endswith("…")

    short_post = an._render_mention(
        {"id": "b", "source_type": "comment", "text": "This is short.",
         "platform": "p", "author_handle": "h", "posted_at": datetime(2026, 7, 1),
         "engagement": {}})
    assert short_post.endswith("This is short.")


def test_the_window_reports_how_much_it_actually_read():
    an._corpus_blob(_corpus(20, 100, source_type="comment"))
    stats = an.corpus_window_stats()
    assert stats["read"] == stats["available"] == 20, "a full read must report as full"
