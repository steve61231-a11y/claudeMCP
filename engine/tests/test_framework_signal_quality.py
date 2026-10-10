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


# --- 5.0 is three things that are happening, not ten headlines ---------------
#
# The section was a feed: up to ten items, each tagged "international media"
# or "social media", in rank order. Read at speed it said nothing, because
# three of those items were one story told by three outlets and the reader
# had to notice that themselves. The client's instruction was to keep three
# slots and group similar items into them, so that three lines carry a whole
# window.

def _win(mention_id, text, engagement, platform="news", hour=2):
    return {
        "id": mention_id, "text": text, "platform": platform,
        "posted_at": NOW - timedelta(hours=hour),
        "engagement": {"likes": engagement}, "source_url": f"http://x/{mention_id}",
    }


WINDOW = [
    _win(1, "Patient advocacy groups publish a county list of facilities turning away registered members", 4120),
    _win(2, "Two counties confirm facilities were asking for cash, blame a reimbursement backlog", 2870),
    _win(3, "Health ministry says a circular on point-of-care charges is being finalised", 1960),
    _win(4, "Chronic illness patients describe rationing medication while waiting on approvals", 3340, "twitter"),
    _win(5, "Oncology unit says approval delays have pushed treatment dates by weeks", 2180, "twitter"),
    _win(6, "Registration passes 4.2 million members, authority says cover does not expire", 640, "twitter"),
]


def test_a_narrative_the_mentions_already_belong_to_names_the_theme():
    """Grouping is done the exact way first. The mentions have already been
    clustered into narratives elsewhere in the report, so a theme can take
    that name — and then the same vocabulary appears in Share of Voice and
    the theme mix instead of three different names for one thing."""
    out = fw.build_emergent_issues(
        WINDOW, now=NOW,
        narratives=[{"label": "SHA rollout pain", "mention_ids": [1, 2, 3]}])
    first = out["themes"][0]
    assert first["label"] == "SHA rollout pain"
    assert first["labelled_by"] == "narrative"
    assert first["count"] == 3


def test_an_inflected_form_is_the_same_word():
    """"approval delays" and "waiting on approvals" are one story and share
    no token at all. A synonym list would have had to be taught that single
    case."""
    out = fw.build_emergent_issues(WINDOW, now=NOW, narratives=[
        {"label": "SHA rollout pain", "mention_ids": [1, 2, 3]}])
    grouped = {t["label"]: t for t in out["themes"]}
    approvals = [t for t in out["themes"] if "approval" in (t["shared_terms"] or [])]
    assert approvals, f"the two approval items were not grouped: {list(grouped)}"
    assert approvals[0]["count"] == 2


def test_one_rare_shared_word_groups_and_one_common_word_does_not():
    """In a corpus about one subject every item says the subject's name, so
    a single shared word is usually a coincidence. A word that only two
    items in the window use is the opposite."""
    out = fw.build_emergent_issues(WINDOW, now=NOW)
    sizes = sorted((t["count"] for t in out["themes"]), reverse=True)
    assert max(sizes) <= 3, (
        "a theme swallowed most of the window — single-link chaining is back")
    assert sum(t["count"] for t in out["themes"]) + out["other_item_count"] == len(WINDOW)


def test_nothing_in_the_window_is_dropped():
    """Three themes and silence about the rest is a claim that the window
    held three things."""
    wide = WINDOW + [
        _win(7, "Unrelated: parliament debates a county revenue formula", 900, "news", 1),
        _win(8, "Unrelated: new import duty on cooking oil announced", 850, "news", 1),
    ]
    out = fw.build_emergent_issues(wide, now=NOW)
    accounted = sum(t["count"] for t in out["themes"]) + out["other_item_count"]
    assert accounted == len(out["items"]), (
        "items in the window are in neither the themes nor the remainder")
    if out["other_item_count"]:
        assert out["other_themes"], "a remainder count with nothing behind it"


def test_theme_shares_are_shares_of_the_window():
    out = fw.build_emergent_issues(WINDOW, now=NOW, narratives=[
        {"label": "SHA rollout pain", "mention_ids": [1, 2, 3]}])
    total = out["window_engagement"]
    for theme in out["themes"]:
        assert abs(theme["share"] - round(100 * theme["engagement"] / total, 1)) < 0.11


def test_a_group_of_one_is_not_named_by_word_frequency():
    """Over one document every word occurs in one of one, so "ranking" them
    is alphabetical order wearing a statistic's clothes — it produced
    "Authority Cover Does" for a headline about registration figures."""
    out = fw.build_emergent_issues([WINDOW[-1]], now=NOW)
    label = out["themes"][0]["label"]
    assert label.lower().startswith("registration"), label
