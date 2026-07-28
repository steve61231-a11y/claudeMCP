"""Regression tests for the 'unhashable type: dict' class of bug.

Scraped JSON payloads sometimes nest a link as a dict ({"href": ...}) or a list
of dicts (RSS/Atom/GDELT). _extract_url must ALWAYS return str|None so the value
can safely flow into set/dedupe logic downstream (it once crashed on `url in
seen_urls`).
"""

from engine.api_server import _extract_url


def _hashable(x) -> bool:
    try:
        hash(x)
        return True
    except TypeError:
        return False


def test_extract_url_always_returns_str_or_none():
    cases = [
        {"url": "https://a.com/1"},                 # plain string
        {"link": {"href": "https://b.com/2"}},      # nested dict
        {"link": [{"href": "https://c.com/3"}]},    # list of dicts
        {"post": {"permalink": "https://d.com/4"}}, # nested under "post"
        {"url": {"foo": "bar"}},                    # dict with no href -> None
        {"title": "no link here"},                  # missing -> None
        "not-even-a-dict",                          # non-dict input -> None
        {"link": [{"nope": 1}, "https://e.com/5"]}, # mixed list
    ]
    for rp in cases:
        result = _extract_url(rp)
        assert result is None or isinstance(result, str), f"{rp!r} -> {result!r}"
        assert _hashable(result), f"unhashable result for {rp!r}: {result!r}"


def test_extract_url_digs_out_the_href():
    assert _extract_url({"link": {"href": "https://x.com/y"}}) == "https://x.com/y"
    assert _extract_url({"url": {"no_href": "z"}}) is None
