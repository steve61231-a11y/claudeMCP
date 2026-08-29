"""The evidence-first pipeline: independence, extraction, findings, review.

The rule these tests exist to defend is NO EVIDENCE, NO CLAIM. Every assertion
that reaches a reader must trace to a stored mention, and nothing may be
promoted from what someone alleged to what is established.
"""

from datetime import datetime, timedelta

import pytest

from engine.evidence import findings as fm
from engine.evidence import records as rm
from engine.evidence.independence import group_duplicates, independence, jaccard, shingles

WIRE = ("Nairobi Senator Edwin Sifuna addressed a rally in Kitale on Sunday, telling "
        "supporters the coalition would field a single candidate in the 2027 election.")
WIRE_EDITED = ("Nairobi Senator Edwin Sifuna addressed a rally in Kitale on Sunday, "
               "telling supporters that the coalition will field one candidate in 2027.")
OTHER = ("The county assembly in Machakos rejected the finance bill after a heated "
         "sitting, sending the budget back to the executive for revision.")


DISTINCT = [
    "The finance committee summoned three principal secretaries over stalled projects.",
    "Traders in Gikomba say the new levy has cut their daily takings sharply.",
    "A court in Milimani extended orders barring the eviction until November.",
    "Teachers in Bungoma began a go-slow over delayed capitation disbursements.",
    "The county tabled an audit showing unexplained spending on a stadium contract.",
    "Matatu operators protested outside City Hall over parking fee increases.",
    "A parliamentary report questioned procurement in the affordable housing scheme.",
    "Residents of Kibra petitioned over water rationing during the dry season.",
    "The ministry defended its fertiliser rollout after farmers reported shortages.",
    "A youth group launched a campaign on unemployment ahead of the budget reading.",
]


def _distinct(i):
    """A genuinely different story per index. Repeating one sentence N times
    would land every item in the same duplicate group and quietly invert the
    thing under test."""
    return DISTINCT[i % len(DISTINCT)] + f" This was the {i}th such report filed."


def _m(mid, text, platform="x", author=None, day=1, url=None):
    return {"id": mid, "text": text, "platform": platform,
            "author_handle": author or platform, "source_url": url,
            "posted_at": datetime(2026, 7, day)}


# --- independence ------------------------------------------------------------

def test_the_same_wire_story_across_outlets_is_one_story():
    """The defect this module exists for: twelve outlets running one wire story
    counted as twelve independent confirmations."""
    corpus = [_m(f"w{i}", WIRE, platform=f"outlet{i}.co.ke", day=i + 1) for i in range(6)]
    corpus.append(_m("o1", OTHER, platform="nation.africa", day=2))
    stats = independence(corpus)
    assert stats["mentions"] == 7
    assert stats["distinct_stories"] == 2
    assert stats["distinct_platforms"] == 7, "seven outlets, but only two stories"
    assert stats["amplification"] == 3.5


def test_lightly_edited_copy_is_still_the_same_story():
    groups = group_duplicates([_m("a", WIRE), _m("b", WIRE_EDITED)])
    assert len(groups) == 1


def test_unrelated_items_are_not_merged():
    groups = group_duplicates([_m("a", WIRE), _m("b", OTHER)])
    assert len(groups) == 2


def test_shared_boilerplate_does_not_make_items_look_alike():
    # Every YouTube description ends the same way. Left in, the subscribe prompt
    # alone would merge unrelated videos into one "story".
    tail = " Subscribe and watch NTV Kenya live for the latest news today and every day."
    groups = group_duplicates([_m("a", "Sifuna in Kitale." + tail),
                               _m("b", "Machakos finance bill rejected." + tail)])
    assert len(groups) == 2


def test_origin_is_the_earliest_item_in_the_group():
    groups = group_duplicates([_m("late", WIRE, day=9), _m("early", WIRE, day=2)])
    assert groups[0].origin_id == "early"


def test_jaccard_and_shingles_behave():
    assert jaccard(shingles(WIRE), shingles(WIRE)) == 1.0
    assert jaccard(shingles(WIRE), shingles(OTHER)) < 0.1


# --- record extraction -------------------------------------------------------

def test_an_allegation_is_never_promoted_to_a_fact():
    kind, status = rm._normalise({"kind": "fact", "status": "alleged"})
    assert kind == rm.KIND_CLAIM and status == rm.STATUS_ALLEGED


def test_an_unrecognised_status_falls_to_unresolved_not_reported():
    _, status = rm._normalise({"kind": "claim", "status": "definitely-true"})
    assert status == rm.STATUS_UNRESOLVED


def test_records_without_a_source_item_are_dropped(monkeypatch):
    """A record pointing at no mention has no provenance and cannot be checked."""
    monkeypatch.setattr(rm.llm, "call_json", lambda *a, **k: {"records": [
        {"i": 1, "kind": "fact", "status": "reported", "statement": "Real one."},
        {"i": 99, "kind": "fact", "status": "reported", "statement": "Orphan."},
    ]})
    out = rm.extract_batch("Sifuna", [_m("m1", WIRE)])
    assert [r.statement for r in out] == ["Real one."]


def test_every_record_carries_its_mention_and_link(monkeypatch):
    monkeypatch.setattr(rm.llm, "call_json", lambda *a, **k: {"records": [
        {"i": 1, "kind": "event", "status": "reported", "statement": "A rally happened.",
         "topic": "Kitale rally", "quote": "addressed a rally"}]})
    out = rm.extract_batch("Sifuna", [_m("m1", WIRE, url="https://n/1")])
    assert out[0].mention_id == "m1"
    assert out[0].url == "https://n/1"
    assert out[0].quote == "addressed a rally"


def test_a_failing_batch_splits_rather_than_losing_the_corpus(monkeypatch):
    seen = []

    def fake(prompt, **kw):
        seen.append(prompt)
        if "[2]" in prompt:
            raise RuntimeError("too long")
        return {"records": [{"i": 1, "kind": "fact", "status": "reported", "statement": "S."}]}

    monkeypatch.setattr(rm.llm, "call_json", fake)
    out = rm.extract_batch("Sifuna", [_m("m1", WIRE), _m("m2", OTHER)])
    assert len(out) == 2, "both halves recovered after the whole batch failed"


# --- findings ----------------------------------------------------------------

def _records(n, topic="cost of living", status=rm.STATUS_REPORTED, platform="nation.africa",
             mention_prefix="m", day_start=1):
    return [rm.EvidenceRecord(
        mention_id=f"{mention_prefix}{i}", kind=rm.KIND_FACT, status=status,
        statement=f"Statement number {i} about {topic}.", topic=topic,
        platform=f"{platform}{i}", posted_at=datetime(2026, 7, day_start + i).isoformat(),
        sentiment="negative") for i in range(n)]


def test_a_finding_reports_independent_stories_not_raw_mentions():
    records = _records(6, platform="outlet")
    # Every mention carries the identical wire text, so it is ONE story.
    mentions = [_m(f"m{i}", WIRE, platform=f"outlet{i}") for i in range(6)]
    built = fm.build_findings(records, mentions, review=False)
    assert built[0].mention_count == 6
    assert built[0].independent_sources == 1
    assert built[0].amplification == 6.0


def test_a_single_origin_can_never_be_high_confidence():
    records = _records(9, platform="outlet")
    mentions = [_m(f"m{i}", WIRE, platform=f"outlet{i}") for i in range(9)]
    built = fm.build_findings(records, mentions, review=False)
    assert built[0].confidence == fm.CONFIDENCE_LOW
    assert "single origin" in built[0].confidence_reason


def test_genuinely_independent_corroboration_earns_high_confidence():
    records = _records(5)
    mentions = [_m(f"m{i}", _distinct(i), platform=f"outlet{i}") for i in range(5)]
    built = fm.build_findings(records, mentions, review=False)
    assert built[0].independent_sources == 5
    assert built[0].confidence == fm.CONFIDENCE_HIGH


def test_opinion_only_evidence_is_never_more_than_low_confidence():
    records = _records(6, status=rm.STATUS_OPINION)
    mentions = [_m(f"m{i}", _distinct(i), platform=f"p{i}") for i in range(6)]
    built = fm.build_findings(records, mentions, review=False)
    assert built[0].confidence == fm.CONFIDENCE_LOW
    assert "not something the corpus establishes" in built[0].confidence_reason


def test_findings_rank_by_independence_not_by_volume():
    loud = _records(8, topic="repeated story", mention_prefix="loud")
    broad = _records(4, topic="independent story", mention_prefix="broad")
    mentions = ([_m(f"loud{i}", WIRE, platform=f"o{i}") for i in range(8)]
                + [_m(f"broad{i}", _distinct(i), platform=f"q{i}") for i in range(4)])
    built = fm.build_findings(loud + broad, mentions, review=False)
    assert built[0].title == "independent story", "the reposted item must not win on volume"


def test_trend_is_reported_with_the_numbers_behind_it():
    early = _records(2, topic="t", mention_prefix="e", day_start=1)
    late = _records(8, topic="t", mention_prefix="l", day_start=15)
    mentions = [_m(r.mention_id, _distinct(i)) for i, r in enumerate(early + late)]
    built = fm.build_findings(early + late, mentions, review=False)
    assert built[0].trend in ("growing", "emerging")
    assert "earlier half" in built[0].trend_detail or "later half" in built[0].trend_detail


def test_a_finding_cannot_exist_without_evidence():
    assert fm.build_findings([], [_m("m1", WIRE)], review=False) == []


# --- contradiction and review ------------------------------------------------

def test_a_refutation_inside_the_cluster_is_moved_out_of_supporting(monkeypatch):
    """Clustering is by topic, so "the project was abandoned" and "the
    contractor returned to site" land in the same cluster. Filing the second as
    SUPPORT for the first is worse than missing it entirely."""
    seen = []

    def fake(prompt, **kw):
        seen.append(prompt)
        listing = prompt.split("Numbered evidence")[1]
        for line in listing.splitlines():
            if "contractor returned" in line:
                return {"contradicting": [int(line.split("]")[0].strip("[ "))],
                        "open_questions": ["Was the project restarted?"]}
        return {"contradicting": [], "open_questions": []}

    monkeypatch.setattr(fm.llm, "call_json", fake)
    monkeypatch.setattr(fm, "challenge", lambda f: {"verdict": "PASS", "reason": "ok"})

    supporters = _records(3, topic="project abandoned", mention_prefix="s")
    opposing = [rm.EvidenceRecord(mention_id="x1", kind=rm.KIND_FACT,
                                  status=rm.STATUS_REPORTED, topic="project restarted",
                                  statement="The contractor returned to site in June.",
                                  platform="nation.africa")]
    mentions = [_m(r.mention_id, _distinct(i)) for i, r in enumerate(supporters + opposing)]
    built = fm.build_findings(supporters + opposing, mentions, min_records=1, review=True)
    target = [f for f in built if f.title == "project abandoned"][0]
    assert target.contradicting, "the contradicting record must be surfaced"
    assert target.open_questions == ["Was the project restarted?"]
    # The cluster's own supporters must not be offered to itself as potential
    # contradiction — a cluster cannot disprove itself.
    assert target.contradicting[0]["statement"] == "The contractor returned to site in June."
    assert "x1" not in [row["mention_id"] for row in target.supporting], \
        "a record that refutes the claim must not also be listed as supporting it"


def test_contradiction_downgrades_confidence():
    finding_level, reason = fm._confidence(6, 3, [rm.STATUS_REPORTED] * 6, contradicting=2)
    assert finding_level == fm.CONFIDENCE_LOW
    assert "contradict" in reason


def test_a_failed_sceptic_pass_is_recorded_as_unreviewed_not_as_a_pass(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(fm.llm, "call_json", boom)
    review = fm.challenge(fm.Finding(title="t", summary="s"))
    assert review["verdict"] == "NOT_REVIEWED"


def test_an_invalid_verdict_is_not_treated_as_a_pass(monkeypatch):
    monkeypatch.setattr(fm.llm, "call_json", lambda *a, **k: {"verdict": "looks fine to me"})
    assert fm.challenge(fm.Finding(title="t"))["verdict"] == "NOT_REVIEWED"


def test_rejected_findings_are_kept_as_an_audit_trail():
    rejected = fm.Finding(title="bad", review={"verdict": "REJECT", "reason": "r"})
    passed = fm.Finding(title="good", review={"verdict": "PASS"})
    assert [f.title for f in fm.unsupported([rejected, passed])] == ["bad"]
