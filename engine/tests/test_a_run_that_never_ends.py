"""An hour, and no analysis.

A live run on Musalia Mudavadi read 819 new mentions, reported "now scoring
sentiment, narratives, voices and network across the whole stored corpus —
this is the long part", and sat there for an hour. The run ceiling is 1800
seconds.

Two faults, and they need different fixes because they are different kinds
of unbounded.

**The ceiling was never asked.** `out_of_time()` existed and was called at
three points — all of them AFTER the long middle. A run could spend any
amount of time on narratives and the network before anything consulted the
clock. This repository's own rule, with the deadline present and unasked.

**The clusterer grows with the corpus.** `build_narratives` embeds every
mention it is handed and then runs HDBSCAN over the result. Both are
CPU-bound and superlinear, and the corpus compounds by design — that IS the
product — so this stage gets slower on every single run, forever.

A deadline cannot fix the second one: neither call can be interrupted
part-way, so a clock would only notice afterwards. The INPUT has to be
bounded, newest first, and the report has to say so — a reader comparing
storylines between runs is entitled to know the window they cover.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "pipeline.py").read_text()


def test_the_ceiling_is_consulted_before_the_long_middle():
    """Not only after it. The three original call sites were all past the
    stage that spent the hour."""
    assert 'out_of_time("narratives")' in PIPELINE, (
        "narrative building, the stage that grows with the corpus, still "
        "runs without the run ceiling getting a vote")
    assert 'out_of_time("network_snapshot")' in PIPELINE

    # And they must come BEFORE the late checks that already existed.
    first_late = PIPELINE.index('out_of_time("entity_event_resolution")')
    assert PIPELINE.index('out_of_time("narratives")') < first_late


def test_a_skipped_stage_degrades_rather_than_crashing():
    """An empty narrative set is something the rest of the pipeline already
    handles. A report delivered without storylines beats one that never
    arrives — and the ledger says which happened."""
    block = PIPELINE[PIPELINE.index('out_of_time("narratives")'):][:400]
    assert "built_narratives = []" in block


def test_the_clusterer_reads_a_bounded_corpus():
    """The fix that actually ends the hour. A cap, not a clock, because
    embed_texts and HDBSCAN cannot be interrupted part-way."""
    assert "narrative_corpus_cap" in PIPELINE, (
        "the clusterer still reads the whole stored corpus, so it gets "
        "slower on every run and nothing stops it")
    from engine.config import settings
    assert settings.narrative_corpus_cap >= 50


def test_it_reads_the_newest_first():
    """A report about the last month must cluster the last month. Taking
    the first N of an arbitrary order would silently report on whatever the
    database happened to return."""
    block = PIPELINE[PIPELINE.index("narrative_corpus_cap"):][:700]
    assert "reverse=True" in block, "the cap takes an arbitrary slice"
    assert "posted_at" in block


def test_the_cap_is_disclosed_when_it_bites():
    """Silently clustering a third of the corpus and presenting the result
    as the storylines is the founding rule broken: a partial reading must
    not look like a complete one."""
    assert 'publish("narrative_corpus"' in PIPELINE
    block = PIPELINE[PIPELINE.index("narrative_corpus_note = ({"):][:700]
    assert '"clustered"' in block and '"available"' in block


def test_nothing_is_disclosed_when_the_cap_does_not_bite():
    """A note on every report, including the ones that read everything,
    is noise — and noise is what an operator learns to skip."""
    block = PIPELINE[PIPELINE.index("narrative_corpus_note = ({"):][:900]
    assert "else None" in block, (
        "the disclosure is unconditional, so it appears on runs that "
        "clustered the whole corpus")
