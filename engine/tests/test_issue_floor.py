"""What the map says when every model call fails.

A run that collects the documents, spends twenty minutes and then prints "not
yet established" in every section is the worst thing this system does. The
documents are sitting there. This is the floor beneath the analysts: names,
dates and recurring terms taken from the corpus by counting, marked as counted.
"""

from engine.reports import issue_floor

CORPUS = [
    {"title": "Okiya Omtatah sues National Treasury over IMF loans",
     "text": "Senator Okiya Omtatah has filed a petition against the National Treasury "
             "and Njuguna Ndungu over IMF-backed borrowing. The Ministry of Health was "
             "not party.",
     "posted_at": "2026-06-01T00:00:00", "source_url": "https://n/1"},
    {"title": "Treasury defends borrowing before Parliament",
     "text": "The National Treasury told Parliament the loans were lawful. "
             "Katiba Institute disagreed.",
     "posted_at": "2026-07-02T00:00:00", "source_url": "https://n/2"},
]


def test_names_are_found_without_a_ner_model():
    """spaCy's NER model is not installed on this deploy, so
    extract_standard_entities returns [] to every caller. That is not a reason
    to hand the reader an empty actor list."""
    names = {a["name"] for a in issue_floor.actors(CORPUS, "Okiya Omtatah")}
    assert "National Treasury" in names
    assert "Njuguna Ndungu" in names
    assert "Katiba Institute" in names
    assert "IMF" in names


def test_a_name_is_never_welded_to_the_next_one():
    names = {a["name"] for a in issue_floor.actors(CORPUS, "Okiya Omtatah")}
    assert "National Treasury and Njuguna Ndungu" not in names
    assert "Parliament The National Treasury" not in names


def test_of_still_joins_a_name():
    assert "Ministry of Health" in issue_floor._proper_names(
        "The Ministry of Health was not party.")


def test_the_principal_is_not_listed_as_one_of_their_own_actors():
    names = {a["name"] for a in issue_floor.actors(CORPUS, "Okiya Omtatah")}
    assert not any("Omtatah" in n for n in names), \
        "'Senator Okiya Omtatah' is the principal even though the name does not contain it"


def test_bare_sentence_openers_are_not_actors():
    names = issue_floor._proper_names("The loans were lawful. Parliament agreed.")
    assert names == [] or all(len(n.split()) > 1 or n.isupper() for n in names)


def test_every_derived_item_carries_its_evidence_and_its_label():
    out = issue_floor.fill({}, CORPUS, "Okiya Omtatah", "IMF")
    assert out["derived_sections"] == ["key_actors", "timeline", "linking_narratives"]
    for section in ("key_actors", "timeline", "linking_narratives"):
        for item in out[section]:
            assert item["derived"] is True
            assert item["quotes"] and item["quotes"][0]["url"]


def test_a_derived_actor_never_claims_a_stance():
    for actor in issue_floor.actors(CORPUS, "Okiya Omtatah"):
        assert actor["position"] == "neutral"
        assert "not established" in actor["relation"]


def test_the_timeline_is_dated_and_in_order():
    events = issue_floor.timeline(CORPUS)
    dates = [e["date"] for e in events]
    assert dates == sorted(dates)
    assert dates == ["2026-06-01", "2026-07-02"]


def test_it_only_fills_what_the_analysts_did_not_return():
    real = {"key_actors": [{"name": "A real actor an analyst wrote"}]}
    out = issue_floor.fill(real, CORPUS, "Okiya Omtatah", "IMF")
    assert out["key_actors"] == real["key_actors"]
    assert "key_actors" not in out["derived_sections"]


def test_an_empty_corpus_invents_nothing():
    out = issue_floor.fill({}, [], "Okiya Omtatah", "IMF")
    assert "derived_sections" not in out
    assert not out.get("key_actors")


def test_a_map_built_with_no_working_model_still_has_content():
    from engine.reports import issue_map

    payload = issue_map.build_issue_map("Okiya Omtatah", "IMF", mentions=CORPUS)
    intersection = payload["intersection"]
    assert intersection["key_actors"], "twenty minutes and an empty form"
    assert intersection["timeline"]
    assert intersection["derived_sections"]
    # And the framework built on top of it is populated too.
    framework = payload["issue_framework"]
    assert framework["main_contours"]["positions"]["neutral"]["total_identified"]
