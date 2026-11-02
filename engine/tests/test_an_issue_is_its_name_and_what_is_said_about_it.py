"""An issue is its name AND what is said about it.

The first live run of the map's record reading (scripts/record_probe.py,
2 Nov 2026) put a member's "let them emulate Safaricom" under the Safaricom
share sale and two speeches on the Rironi-Mau Summit ROAD under Mau forest
evictions; and every document naming Safaricom and the government had been
counted as the map's intersection itself. The map matched an issue on its
name alone, because the name was one of the terms a document could carry.

`reports/issue_match.py` is the one rule for the pools, the connection and
the records: a phrase naming the issue whole; or every name (an acronym or
its expansion) WITH at least one of the other words as a source writes them;
or, for an issue with no name in it, every distinctive word.
"""

import pytest

from engine.evidence import connection
from engine.reports import issue_match, relevance


@pytest.mark.parametrize("issue,text,expected", [
    # The live failures.
    ("Safaricom shares sale", "In fact, let them emulate Safaricom.", False),
    ("Mau forest evictions", "The Rironi-Mau Summit Road is under construction.", False),
    ("the IMF loan", "IMF governance diagnostic report", False),
    # The name, with what is said about it, as sources write it.
    ("Safaricom shares sale", "Treasury will sell 15 per cent of Safaricom to Vodacom.", True),
    ("Safaricom shares sale", "the sale of Safaricom shares", True),
    ("Safaricom shares sale", "Safaricom posted record profits.", False),
    ("Mau forest evictions", "Families evicted from the Mau were stranded.", True),
    ("Mau forest evictions", "Evictions in Embobut forest continued.", False),
    ("the IMF loan", "Kenya borrowed from the International Monetary Fund.", True),
    ("the IMF loan", "the IMF programme review", True),
    ("Kenya Kwanza budget cuts", "Kenya Kwanza rally in Nyeri", False),
    ("Kenya Kwanza budget cuts", "The Kenya Kwanza government slashed the budget.", True),
    ("Adani JKIA deal", "Adani energy KETRACO deal", False),
    ("Adani JKIA deal", "Adani's proposal to take over Jomo Kenyatta International Airport", True),
    ("Gen Z protests", "Gen Z voters registered in Nairobi", False),
    ("Gen Z protests", "Gen Z took to the streets in demonstrations", True),
    # An issue that is its name needs nothing more.
    ("SHA", "registering members of the Social Health Authority", True),
    ("SHA", "SHA-256 hashes", True),
    # An issue with no name in it: every distinctive word, as written.
    ("school fees", "The school fees balance was unpaid.", True),
    ("school fees", "hospital fees for the patient", False),
    ("teachers strike", "Teachers downed tools on Monday.", True),
    ("teachers strike", "the doctors' strike", False),
    ("Housing levy", "remit statutory deductions including the Housing Levy", True),
    ("Housing levy", "Levi House Construction", False),
    # What Kenyan sources call it, as a whole.
    ("Housing levy", "The Affordable Housing Act was assented to.", True),
    ("school fees", "capitation for day secondary schools", True),
])
def test_an_issue_is_its_name_and_what_is_said_about_it(issue, text, expected):
    assert issue_match.of(issue).names(text) is expected


def test_the_connection_uses_the_same_rule():
    assert not connection.about_issue("Ruto said Kenyans should emulate Safaricom.", "Safaricom shares sale")
    assert connection.about_issue("Ruto defended selling the Safaricom stake.", "Safaricom shares sale")


def test_a_document_naming_only_the_name_is_set_aside_and_says_so():
    the_match = issue_match.of("Safaricom shares sale")
    docs = [{"text": "Safaricom posted record profits in Nairobi."},
            {"text": "Treasury confirmed the sale of the Government's Safaricom shares to Vodacom."}]
    pools, report = relevance.partition_corpus(docs, ["Treasury"], ["Safaricom"], False, issue_test=the_match)
    assert len(pools[relevance.POOL_CORE]) == 1
    assert report["reasons"] == {"names the issue's name without the issue": 1}


def test_an_acronym_alone_is_flagged_where_it_may_be_initials():
    m = issue_match.of("SHA")
    assert m.acronym_only("JMA v SHA") == "SHA"
    assert m.acronym_only("Abdille v Social Health Authority (SHA)") is None


def test_what_a_document_had_to_carry_is_said_in_words():
    assert issue_match.of("Safaricom shares sale").describe()[-1] == "safaricom with shares or sale"
    assert "school + fees" in issue_match.of("school fees").describe()
