"""Every kind of issue is searched as its record prints it — not only the
one the fixes were found on.

Phase 0 was found on one live map (the Safaricom share sale). The founder:
"it's not only the Safaricom issue … it could be SHA, it could be
Safaricom … it could be related to schools, the government. Please be
optimizing for any other issue we'll ever search." A battery of issues of
every kind was run through the map's reading of an issue, and it showed the
defects were general: "school", "county", "strike" and "model" each counted
a document as about the issue; "Housing levy" made every housing story
about it; "e-Citizen" became "Citizen", which is Citizen TV; "Gen Z"
became "Gen"; "SHA" was never spelled out, so it also found SHA-256; and
every issue got the same five research angles whatever decides it.

These pin the PROPERTIES, across kinds, so the next fix found on one issue
cannot quietly break the others.
"""

import pytest

from engine.reports import decompose, issue_profile

BATTERY = [
    # issue, kinds that must be read, an institution that must be asked
    ("SHA", {"health"}, "Ministry of Health"),
    ("NHIF to SHA transition", {"health"}, "Social Health Authority"),
    ("school fees", {"education"}, "Ministry of Education"),
    ("teachers strike", {"education"}, "Teachers Service Commission"),
    ("CBC curriculum", {"education"}, "Ministry of Education"),
    ("KUCCPS placement", {"education"}, "Ministry of Education"),
    ("university funding model", {"education"}, "Ministry of Education"),
    ("HELB loans", {"education"}, "Ministry of Education"),
    ("Housing levy", {"public_money"}, "National Treasury"),
    ("Finance Bill 2025", {"legislation"}, "National Assembly"),
    ("county allocations", {"devolution"}, "Commission on Revenue Allocation"),
    ("abductions", {"security_rights"}, "IPOA"),
    ("Shakahola massacre", {"security_rights"}, "National Police Service"),
    ("Mau forest evictions", {"land_environment"}, "Kenya Forest Service"),
    ("land grabbing in Karen", {"land_environment"}, "National Land Commission"),
    ("Kenya Power blackouts", {"energy"}, "EPRA"),
    ("Adani JKIA deal", {"transport"}, "Kenya Airports Authority"),
    ("maize prices", {"agriculture"}, "NCPB"),
    ("the IMF loan", {"international"}, "International Monetary Fund"),
    ("Haiti mission", {"international"}, "National Police Service"),
    ("Kenyan Court stopping the Kenyan government's sale of its Safaricom shares to Vodacom",
     {"legal", "corporate"}, "High Court"),
]


@pytest.mark.parametrize("issue,kinds,institution", BATTERY)
def test_every_issue_is_read_for_its_kind_and_the_bodies_that_decide_it(issue, kinds, institution):
    p = issue_profile.profile(issue)
    assert kinds <= set(p["kinds"]), f"{issue}: read as {p['kinds']}"
    assert institution in p["institutions"], f"{issue}: asks {p['institutions']}"
    assert p["search_terms"], f"{issue}: nothing to search for"


@pytest.mark.parametrize("issue", [b[0] for b in BATTERY])
def test_no_issue_is_matched_on_an_everyday_word_alone(issue):
    p = issue_profile.profile(issue)
    for identity in p["identities"]:
        if " " in identity or "-" in identity or identity.isupper():
            continue
        assert not decompose.is_common_word(identity), \
            f"{issue}: '{identity}' alone would count half the news as about the issue"
        assert identity.lower() not in ("kenyan", "kenya"), f"{issue}: a demonym is not an issue"


@pytest.mark.parametrize("issue", [b[0] for b in BATTERY])
def test_no_typed_sentence_and_no_leading_article_is_searched(issue):
    terms = issue_profile.profile(issue)["search_terms"]
    if decompose.is_sentence(issue):
        assert issue not in terms
    assert not any(t.lower().startswith("the ") for t in terms)


@pytest.mark.parametrize("issue,expansion", [
    ("SHA", "Social Health Authority"), ("HELB loans", "Higher Education Loans Board"),
    ("KUCCPS placement", "Kenya Universities and Colleges Central Placement Service"),
    ("Housing levy", "Affordable Housing Levy"), ("Haiti mission", "Multinational Security Support"),
    ("the IMF loan", "International Monetary Fund"), ("abductions", "enforced disappearances"),
])
def test_what_an_issue_is_also_called_is_searched_too(issue, expansion):
    assert expansion in issue_profile.profile(issue)["search_terms"]


@pytest.mark.parametrize("text,name", [
    ("e-Citizen revenue", "e-Citizen"), ("M-Pesa charges", "M-Pesa"), ("Gen Z protests", "Gen Z")])
def test_a_name_is_kept_whole(text, name):
    ids = decompose.decompose(text)["identities"]
    assert name in ids
    assert "Citizen" not in ids and "Gen" not in ids


def test_institutions_come_from_every_kind_in_turn_most_specific_first():
    p = issue_profile.profile("school fees")
    assert p["kinds"][0] == "education"
    assert p["institutions"][:2] == ["Ministry of Education", "National Treasury"]


def test_every_acronym_expansion_is_a_kenyan_name_not_a_guess():
    for acronym, names in issue_profile.ACRONYMS.items():
        assert acronym.isupper() and names, acronym
        for name in names:
            assert name[:1].isupper(), (acronym, name)
