"""It seems to make mistakes when it comes to identifying local and
international coverage.

An investor, about a live report — and it did, systematically. Google News
items stored the outlet's NAME squashed into a key ("thestar",
"nationafrica"), and the classifier looked for DOMAINS like the-star.co.ke,
so the Daily Nation was counted as international press. Half of Kenya's big
newsrooms are not on .ke at all. A Kenyan broadcaster's YouTube channel read
as a social post, and Google News's "Facebook" outlet read as foreign press.

Every case below is a real outlet from the sources' own answers on
21 Oct 2026 (scripts/data_probe.py), not an imagined one.
"""

import pytest

from engine.reports import outlets
from engine.reports.sentiment_framework import build_overall_mentions


def _news(outlet, domain=None, platform=None, country=None):
    return {"platform": platform or outlet.lower().replace(" ", ""), "source_type": "article",
            "outlet": outlet, "outlet_domain": domain, "source_country": country}


@pytest.mark.parametrize("outlet,domain", [
    ("Daily Nation", "nation.africa"),
    ("Citizen Digital", "citizen.digital"),
    ("People Daily", "peopledaily.digital"),
    ("Capital FM Africa", "capitalfm.africa"),
    ("K24 Digital", "k24.digital"),
    ("TV47 Digital", "tv47.digital"),
    ("Business Daily", "businessdailyafrica.com"),
    ("The Kenya Times", "thekenyatimes.com"),
    ("The Star, Kenya", "the-star.co.ke"),
    ("Kenya News Agency", "kenyanews.go.ke"),
    ("Kenyans.co.ke", "kenyans.co.ke"),
])
def test_kenyan_newsrooms_are_local_whatever_their_domain(outlet, domain):
    assert outlets.classify(_news(outlet, domain))[0] == outlets.LOCAL


@pytest.mark.parametrize("outlet", [
    "Daily Nation", "The Star, Kenya", "Citizen Digital", "The Standard",
    "People Daily", "Kenyans.co.ke", "Tuko News", "The Eastleigh Voice", "KBC Digital",
])
def test_rows_stored_before_domains_were_kept_still_classify(outlet):
    """Old Google News rows carry only the name and a squashed key."""
    assert outlets.classify(_news(outlet))[0] == outlets.LOCAL


@pytest.mark.parametrize("outlet,domain", [
    ("BBC", "bbc.com"), ("Reuters", "reuters.com"), ("Al Jazeera", "aljazeera.com"),
    ("The Economist", "economist.com"), ("The Citizen", "thecitizen.co.tz"),
])
def test_foreign_outlets_are_international(outlet, domain):
    assert outlets.classify(_news(outlet, domain))[0] == outlets.INTERNATIONAL


def test_gdelt_s_source_country_decides_an_unknown_outlet():
    local = _news("kenya-today.com", "someobscure.com", country="Kenya")
    foreign = _news("allafrica.com", "allafrica.com", country="Nigeria")
    assert outlets.classify(local)[0] == outlets.LOCAL
    assert outlets.classify(foreign)[0] == outlets.INTERNATIONAL


@pytest.mark.parametrize("channel,expected", [
    ("NTV Kenya", outlets.LOCAL), ("Citizen TV Kenya", outlets.LOCAL),
    ("KTN News Kenya", outlets.LOCAL), ("Parliament of Kenya", outlets.LOCAL),
    ("Al Jazeera English", outlets.INTERNATIONAL), ("BBC News Africa", outlets.INTERNATIONAL),
    ("HESSIE JAY PRODUCTION", outlets.SOCIAL), ("Kenya Digital News", outlets.SOCIAL),
])
def test_the_publisher_decides_on_a_platform_anyone_can_post_to(channel, expected):
    video = {"platform": "youtube", "source_type": "video", "author_handle": channel}
    assert outlets.classify(video)[0] == expected


def test_a_reaction_is_social_and_a_facebook_post_is_not_foreign_press():
    assert outlets.classify({"platform": "youtube", "source_type": "comment",
                             "author_handle": "NTV Kenya"})[0] == outlets.SOCIAL
    assert outlets.classify(_news("Facebook", "facebook.com"))[0] == outlets.SOCIAL


def test_every_call_says_what_decided_it():
    _, basis = outlets.classify(_news("Some Blog", "someblog.example"))
    assert "default" not in basis and "someblog.example" in basis
    _, basis = outlets.classify({"platform": "unknown", "source_type": "article"})
    assert "default" in basis, "a guess must say it is a guess"


def test_sources_are_listed_by_the_name_a_reader_knows():
    mentions = [
        {"id": 1, **_news("Daily Nation", "nation.africa", platform="dailynation")},
        {"id": 2, "platform": "youtube", "source_type": "comment", "author_handle": "@a"},
        {"id": 3, "platform": "youtube", "source_type": "comment", "author_handle": "@b"},
        {"id": 4, "platform": "youtube", "source_type": "video", "author_handle": "NTV Kenya"},
    ]
    result = build_overall_mentions({}, mentions, None, {})
    names = {s["source"]: s["outlet_type"] for s in result["sources_detail"]}
    assert names == {"Daily Nation": "local_media", "YouTube comments": "social_media",
                     "NTV Kenya": "local_media"}
    shares = {s["outlet_type"]: s["count"] for s in result["segmentation"]}
    assert shares == {"local_media": 2, "international_media": 0, "social_media": 2}
    assert result["segmentation_basis"], "the split cannot be audited"


def test_the_dashboard_uses_the_same_classification():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    channels = page[page.index("function dashChannels"):page.index("function namesInPlay")]
    assert "sources_detail" in channels and "outlet_type" in channels
