"""Stakeholders are institutions and individuals, not the press that reported them.

A live map on the Safaricom share sale listed its challengers as "@Kenyan
Daily Report Tv", "@MWANAKENYA MEDIA", "@REN MEDIA" and two YouTube
commenters — the channels that covered the ruling and the people who
replied under them. The founder: "No need for media outlets, just
institutional and individual stakeholders." They are set aside with a
reason (shown to the operator), never silently; a broadcaster that is
itself a party to the issue stays.
"""

import pytest

from engine.reports import issue_framework as ifw


@pytest.mark.parametrize("actor,press", [
    ({"name": "@Kenyan Daily Report Tv", "relation": "A YouTube channel that reported the ruling"}, True),
    ({"name": "@@mabkak", "relation": "A YouTube commenter who wrote 'God is with the Kenyans'"}, True),
    ({"name": "REN MEDIA", "relation": "reported the High Court cancelled the deal"}, True),
    ({"name": "Daily Nation", "relation": "reported the sale"}, True),
    ({"name": "Kenyan Court (Court of Appeal/High Court)", "relation": "nullified the sale"}, False),
    ({"name": "Vodacom", "relation": "the intended buyer"}, False),
    ({"name": "Nation Media Group", "relation": "was sued by the government over the coverage"}, False),
    ({"name": "Kenya Government (Administration of President William Ruto)", "relation": "sold the shares"}, False),
])
def test_the_press_is_told_from_a_party(actor, press):
    assert bool(ifw.is_media_actor(actor)) is press


def test_what_is_set_aside_is_declared():
    kept, removed = ifw.without_media([{"name": "@REN MEDIA", "relation": "x"}, {"name": "Vodacom"}])
    assert [a["name"] for a in kept] == ["Vodacom"]
    assert removed == [{"name": "@REN MEDIA", "reason": "a social media account"}]
