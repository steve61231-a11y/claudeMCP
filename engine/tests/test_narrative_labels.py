"""Narratives must be readable and openable.

A live report came back with every narrative named "narrative-3", "narrative-17"
— unusable, and indistinguishable from a working run. The cause was N concurrent
labelling calls all failing on one rate limit, falling back to a numbered
placeholder. These tests pin both halves of the fix: one batched call, and a
floor that is always a real name.
"""

import pytest

from engine.intelligence import narratives as nar


class _Mention(dict):
    pass


def _mention(mid, text, engagement=None, url=None, author=None):
    from datetime import datetime
    return {"id": mid, "text": text, "posted_at": datetime(2026, 7, 1),
            "engagement": engagement or {"likes": 1}, "source_url": url,
            "author_handle": author, "platform": "youtube"}


# --- the floor ---------------------------------------------------------------

def test_derived_label_names_the_story_not_a_number():
    label, description = nar.derived_label([
        "Sifuna holds a mega rally in Kitale",
        "Kitale rally draws huge crowds for Sifuna",
        "Mega rally at Kitale: Sifuna makes an announcement",
    ], exclude={"sifuna"})
    assert "Kitale" in label
    assert not label.lower().startswith("narrative")
    assert description


def test_derived_label_ignores_the_subject_name():
    label, _ = nar.derived_label(["Ruto speaks", "Ruto again", "Ruto once more"],
                                 exclude={"ruto"})
    assert "Ruto" not in label


def test_derived_label_ranks_by_documents_not_repetition():
    # One long item repeating a word must not name the cluster on its own.
    texts = ["budget " * 50, "Kitale rally turnout", "Kitale rally crowds"]
    label, _ = nar.derived_label(texts)
    assert "Kitale" in label


def test_numbered_placeholders_are_rejected_as_labels():
    assert nar._looks_useless("narrative-3")
    assert nar._looks_useless("Cluster 12")
    assert nar._looks_useless("theme_7")
    assert nar._looks_useless("   ")
    assert not nar._looks_useless("Kitale mega rally")


# --- batching ----------------------------------------------------------------

def test_all_clusters_are_labelled_in_one_call(monkeypatch):
    calls = []

    def fake(system, user, expected_keys=None, max_tokens=None):
        calls.append(user)
        return {"clusters": [{"id": 0, "label": "Kitale rally", "description": "d"},
                             {"id": 1, "label": "TIFA poll surge", "description": "d"}]}

    monkeypatch.setattr(nar.llm, "call_json_untrusted", fake)
    out = nar.label_clusters([(0, ["a"]), (1, ["b"])])
    assert len(calls) == 1, "one request for the whole set, not one per cluster"
    assert out[0]["label"] == "Kitale rally"
    assert out[1]["label"] == "TIFA poll surge"


def test_a_failing_batch_splits_instead_of_losing_everything(monkeypatch):
    def fake(system, user, expected_keys=None, max_tokens=None):
        if "Cluster 0" in user and "Cluster 1" in user:
            raise RuntimeError("rate limited")
        cid = 0 if "Cluster 0" in user else 1
        return {"clusters": [{"id": cid, "label": f"Real story {cid}", "description": ""}]}

    monkeypatch.setattr(nar.llm, "call_json_untrusted", fake)
    out = nar.label_clusters([(0, ["a"]), (1, ["b"])])
    assert out[0]["label"] == "Real story 0"
    assert out[1]["label"] == "Real story 1"


def test_model_returning_a_placeholder_is_rejected(monkeypatch):
    monkeypatch.setattr(nar.llm, "call_json_untrusted", lambda *a, **k: {
        "clusters": [{"id": 0, "label": "Cluster 0", "description": "d"}]})
    assert nar.label_clusters([(0, ["a"])]) == {}


# --- end to end --------------------------------------------------------------

def _clustered(monkeypatch, labels):
    monkeypatch.setattr(nar, "cluster_mentions", lambda texts: labels)


def test_total_labelling_failure_never_emits_a_numbered_narrative(monkeypatch):
    """The exact production defect: the provider is down, and the report must
    still name its narratives from the text rather than by index."""
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(nar.llm, "call_json_untrusted", boom)
    _clustered(monkeypatch, [0, 0, 0])
    built = nar.build_narratives([
        _mention("m1", "Sifuna holds a mega rally in Kitale"),
        _mention("m2", "Kitale rally draws crowds"),
        _mention("m3", "The Kitale rally continues"),
    ], subject_terms={"sifuna"})

    assert len(built) == 1
    label = built[0]["label"]
    assert not label.lower().startswith("narrative")
    assert "Kitale" in label
    assert built[0]["labelled_by"] == "derived"


def test_narratives_carry_the_mentions_behind_them(monkeypatch):
    monkeypatch.setattr(nar.llm, "call_json_untrusted", lambda *a, **k: {
        "clusters": [{"id": 0, "label": "Kitale rally", "description": "d"}]})
    _clustered(monkeypatch, [0, 0, 0])
    built = nar.build_narratives([
        _mention("m1", "Rally one", {"views": 100}, url="https://x/1", author="ktn"),
        _mention("m2", "Rally two", {"views": 900}, url="https://x/2", author="ntv"),
        _mention("m3", "Rally three", {"views": 5}, url=None, author="k24"),
    ])
    evidence = built[0]["evidence"]
    assert [e["mention_id"] for e in evidence] == ["m2", "m1", "m3"], "loudest first"
    assert evidence[0]["url"] == "https://x/2"
    assert evidence[0]["author"] == "ntv"
    assert evidence[0]["excerpt"] == "Rally two"
    # A missing link must stay missing rather than being invented.
    assert evidence[2]["url"] is None


def test_labelled_by_records_which_path_named_each_narrative(monkeypatch):
    monkeypatch.setattr(nar.llm, "call_json_untrusted", lambda *a, **k: {
        "clusters": [{"id": 0, "label": "Kitale rally", "description": "d"}]})
    _clustered(monkeypatch, [0, 0, 0])
    built = nar.build_narratives([_mention(f"m{i}", "Kitale rally") for i in range(3)])
    assert built[0]["labelled_by"] == "model"
