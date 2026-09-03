"""Discovery is the single richest source of depth in an issue map, and it
returned nothing on every run it ever made.

`fetch_documents(subject_name, aliases, queries)` keeps a discovered page only
if the page actually names the subject. The issue map called it as
`fetch_documents(discovery_queries[0], [], discovery_queries)` — so the
"subject name" it checked against was the literal query string

    "Okiya Omtatah" "International Monetary Fund (IMF)"

quote marks included, which appears in no page on earth, and the surname it
derived from that was `(imf)`. Every fetched document failed the check. The
symptom was a corpus of twelve documents on a question with plenty of coverage.
"""

import pytest

from engine.reports import issue_map


class _Recorder:
    def __init__(self):
        self.call = None

    def fetch_documents(self, subject_name, aliases, queries):
        self.call = {"subject": subject_name, "aliases": list(aliases),
                     "queries": list(queries)}
        return [{"text": "doc"}]


@pytest.fixture()
def discovery(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(issue_map.settings, "enable_discovery", True)
    monkeypatch.setattr(issue_map.settings, "searxng_url", "http://searx.test")
    monkeypatch.setattr("engine.ingestion.discovery_connector.DiscoveryConnector",
                        lambda *a, **k: rec)
    return rec


QUERIES = ['"Okiya Omtatah" "International Monetary Fund (IMF)"',
           '"Omtatah" "IMF" court']


def test_the_subject_is_the_principal_not_the_first_query(discovery):
    issue_map.acquire_intersection_documents(
        QUERIES, subject_name="Odious Debt case by Okiya Omtatah",
        aliases=["Okiya Omtatah", "Omtatah", "IMF"])
    assert discovery.call["subject"] == "Odious Debt case by Okiya Omtatah"
    assert '"' not in discovery.call["subject"], \
        "a quoted search query can never appear in page text"
    assert "Okiya Omtatah" in discovery.call["aliases"]


def test_every_query_still_reaches_the_sweep(discovery):
    issue_map.acquire_intersection_documents(QUERIES, subject_name="P")
    assert discovery.call["queries"] == QUERIES


def test_it_still_works_when_no_subject_is_given(discovery):
    """Older callers passed only queries; they must not start crashing."""
    assert issue_map.acquire_intersection_documents(QUERIES)
    assert discovery.call["subject"] == QUERIES[0]


def test_discovery_is_skipped_rather_than_fatal_when_disabled(monkeypatch):
    monkeypatch.setattr(issue_map.settings, "enable_discovery", False)
    assert issue_map.acquire_intersection_documents(QUERIES, subject_name="P") == []


def test_the_research_dimensions_are_actually_searched():
    """The plan was published to the page. It also has to reach the sweep."""
    from engine.reports import decompose

    dims = decompose.research_dimensions("Okiya Omtatah", "IMF")
    planned = {q for d in dims for q in d["queries"]}
    merged = set(issue_map._merge_queries(['"Okiya Omtatah" "IMF"'], planned))
    assert planned <= merged


def test_merging_queries_folds_case_and_keeps_order():
    merged = issue_map._merge_queries(['"a" "b"', "x"], ['"A" "B"', "y"])
    assert merged == ['"a" "b"', "x", "y"]
