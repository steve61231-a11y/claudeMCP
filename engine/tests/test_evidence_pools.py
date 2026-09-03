"""The gate that emptied the map.

"Odious debt case by Okiya Omtatah" × "International Monetary Fund (IMF)" kept
TWO documents out of hundreds, and five analysts then spent ten minutes reading
two articles. The cause was a hard AND: every document had to name both halves
or it was thrown away. On a real question almost nothing does.

An investigator reads what exists on the person, what exists on the issue, and
the places the two touch — and finds the third only because they read the first
two. So the AND is now a sort, not a gate, and every item carries the pool it
came from so an analyst can never mistake background for a connection.
"""

from engine.reports import analysts, digest, relevance

IDENTITIES = ["Okiya Omtatah", "Omtatah"]
ISSUE = ["International Monetary Fund", "IMF"]

CORPUS = [
    {"text": "Okiya Omtatah sues over IMF-backed loans in Kenya"},
    {"text": "Omtatah in Nairobi court over county funds"},
    {"text": "Omtatah petitions on the sugar levy"},
    {"text": "IMF programme review for Kenya concludes"},
    {"text": "Oregon state forestry board meets"},
    {"text": ""},
]


def test_the_pools_split_three_ways_and_keep_the_background():
    pools, report = relevance.partition_corpus(CORPUS, IDENTITIES, ISSUE, False)
    assert report["pools"] == {"core": 1, "principal_side": 2, "issue_side": 1}
    # The old gate kept one of these six. Four are usable.
    assert report["kept"] == 4


def test_off_topic_is_still_dropped_and_says_why():
    _pools, report = relevance.partition_corpus(CORPUS, IDENTITIES, ISSUE, False)
    assert report["dropped"] == 2
    assert "mentions neither the principal nor the issue" in report["reasons"]
    assert "empty document" in report["reasons"]


def test_every_kept_document_is_stamped_with_its_pool():
    pools, _r = relevance.partition_corpus(CORPUS, IDENTITIES, ISSUE, False)
    for name, items in pools.items():
        for item in items:
            assert item["evidence_pool"] == name


def test_the_market_anchor_still_applies_to_generic_pairs():
    docs = [{"text": "The senate debated forestry policy in Oregon"}]
    pools, report = relevance.partition_corpus(docs, ["senate"], ["forestry"], True)
    assert not any(pools.values())
    assert any("market" in reason for reason in report["reasons"])


def test_the_analyst_is_told_what_each_document_can_support():
    line = analysts._render_mention(
        {"id": "abcdef12", "text": "t", "platform": "nation.africa",
         "source_type": "article", "evidence_pool": "principal_side"})
    assert "evidence=principal_side" in line
    # And the prompt says what that tag means, and forbids the bridge.
    preamble = analysts.ISSUE_PREAMBLE
    assert "evidence=core" in preamble
    assert "principal_side" in preamble and "issue_side" in preamble
    assert "Never combine" in preamble


def test_an_unsorted_document_carries_no_tag():
    """A report run does not sort into pools; it must not gain a phantom one."""
    line = analysts._render_mention({"id": "abcdef12", "text": "t"})
    assert "evidence=" not in line


def test_documents_naming_both_halves_are_read_first():
    """Ordering by engagement alone buried the handful of items that establish
    the connection under a hundred background articles, in a later chunk a
    failed call could take out entirely."""
    mentions = [
        {"id": "a", "text": "background " * 5, "evidence_pool": "issue_side",
         "engagement": {"views": 10000}},
        {"id": "b", "text": "the connection " * 5, "evidence_pool": "core",
         "engagement": {"views": 1}},
    ]
    chunks = digest._chunk_mentions(mentions, budget=100000)
    assert chunks[0][0]["id"] == "b"


def test_blending_puts_the_intersection_first_and_caps_the_background():
    from engine.reports import issue_map

    pools = {
        "core": [{"text": f"c{i}", "posted_at": "2026-01-01"} for i in range(3)],
        "principal_side": [{"text": f"p{i}", "posted_at": "2026-01-01"}
                           for i in range(500)],
        "issue_side": [{"text": f"i{i}", "posted_at": "2026-01-01"} for i in range(500)],
    }
    blended = issue_map._blend_pools(pools)
    assert [d["text"] for d in blended[:3]] == ["c0", "c1", "c2"]
    assert len(blended) == 3 + issue_map.POOL_BUDGET["principal_side"] \
        + issue_map.POOL_BUDGET["issue_side"]
