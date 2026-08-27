"""The flagship sections have to carry signal, not the loudest noise.

5.0 Emergent issues sorted on raw engagement. A YouTube video carries a view
count in the hundreds of thousands and a newspaper article carries none at all,
so editorial coverage lost every comparison and never appeared. Every slot went
to engagement-farmed rage-bait wrapped in pleas to subscribe, while 238 news
items in the same window were invisible.

1.0 Summary of subject read `payload["subject_profile"]`, which nothing ever
wrote — so a head of state was described as a "public figure" while his
encyclopedia entry sat unread in the corpus.
"""

from datetime import datetime, timedelta

from engine.reports import sentiment_framework as fw

NOW = datetime(2026, 8, 26, 12, 0)


def _item(text, platform, engagement, hours_ago=1):
    return {
        "text": text,
        "platform": platform,
        "posted_at": NOW - timedelta(hours=hours_ago),
        "engagement": {"views": engagement},
        "source_url": f"https://{platform}/x",
        "source_type": "video" if platform == "youtube" else "article",
    }


def test_editorial_coverage_is_never_crowded_out_by_view_counts():
    """The exact production shape: ten viral videos and a handful of articles."""
    mentions = [
        _item(f"Ruto Finished: Uhuru COmpletely Destroys Ruto {i}. Subscribe now!",
              "youtube", 100_000 - i)
        for i in range(10)
    ] + [
        _item("Treasury revises budget ceiling ahead of finance bill", "nation.africa", 0),
        _item("Parliament summons CS over housing levy", "standardmedia.co.ke", 0),
    ]
    out = fw.build_emergent_issues(mentions, now=NOW)
    segments = {i["outlet_type"] for i in out["items"]}
    assert "social_media" in segments
    assert len(segments) > 1, "one platform took every slot again"
    headlines = " ".join(i["headline"] for i in out["items"])
    assert "Treasury revises budget" in headlines


def test_self_promotion_sinks_within_its_own_segment():
    """A channel pitch is an advertisement for the uploader, not coverage."""
    mentions = [
        _item("BREAKING: Ruto finished! Subscribe now, join this channel to get access to perks",
              "youtube", 90_000),
        _item("President addresses the nation on the health financing transition",
              "youtube", 40_000),
    ]
    out = fw.build_emergent_issues(mentions, now=NOW)
    social = [i for i in out["items"] if i["outlet_type"] == "social_media"]
    assert "President addresses the nation" in social[0]["headline"], (
        "the channel pitch still outranked real coverage"
    )


def test_a_single_subscribe_does_not_bury_genuine_coverage():
    """Demote, never exclude — a real news video may still say subscribe once."""
    mentions = [
        _item("President addresses the nation on health financing. Subscribe to NTV Kenya.",
              "youtube", 50_000),
    ]
    out = fw.build_emergent_issues(mentions, now=NOW)
    assert len(out["items"]) == 1, "a legitimate item was suppressed rather than demoted"


def test_promo_ratio_reads_self_promotion_not_anger():
    """It must not become an opinion filter: a furious editorial stays."""
    assert fw.promo_ratio("Subscribe now! Join this channel to get access to perks") > 0
    assert fw.promo_ratio(
        "The president's housing levy is an indefensible burden on households "
        "and parliament should reject it outright."
    ) == 0


# --- 1.0 identity -----------------------------------------------------------

def test_the_subject_is_described_from_reference_material():
    mentions = [
        {"source_type": "reference", "raw_payload": {"relation": "subject"},
         "text": "William Ruto is a Kenyan politician who has served as the fifth "
                 "president of Kenya since September 2022. He previously served as "
                 "deputy president."},
    ]
    profile = fw.subject_profile_from_corpus(mentions)
    assert "fifth president of Kenya" in profile


def test_linked_entities_are_not_mistaken_for_the_subject():
    """The connector files linked-entity summaries the same way; describing the
    subject as one of them would be worse than saying nothing."""
    mentions = [
        {"source_type": "reference", "raw_payload": {"relation": "linked_entity"},
         "text": "The Kenya Revenue Authority is the tax collection agency of Kenya "
                 "established in 1995 by an act of parliament."},
    ]
    assert fw.subject_profile_from_corpus(mentions) is None


def test_no_reference_material_means_no_claim():
    assert fw.subject_profile_from_corpus([]) is None
    assert fw.subject_profile_from_corpus(
        [{"source_type": "article", "text": "Ruto spoke today about the budget."}]
    ) is None
