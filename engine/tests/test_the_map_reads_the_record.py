"""The Issue Map reads the records a connection is made on.

Phase 1 of docs/ISSUE_MAP_FOUNDATION.md. Each connector is written against a
contract the source probe OBSERVED on 2 Nov 2026 (scripts/source_probe.py,
from GitHub's network) and confirmed reachable from the live server
(evidence/record_reach.py, /api/health): Kenya Law's search API answers JSON
with result cards (`format=json` is refused; `nature=` filters nothing);
Parliament's pages carry the ~29 most recent Hansard and order-paper PDFs;
the World Bank's APIs key projects and documents by id. Mzalendo's Hansard
API was ruled out by the same probe: it ignores search and ordering and its
newest sitting is July 2025. Tests never touch the network.
"""

from datetime import datetime

import pytest

from engine.ingestion import kenya_law_records, parliament_records, world_bank_records
from engine.reports import issue_map

CARD = """<ul class="list-unstyled search-result-list"> <li class="mb-4 hit " data-document-id="1">
<div class="card"><div class="card-body"><h5 class="card-title">
<a class="h5 text-primary" href="/akn/ke/judgment/kehc/2024/8429/eng@2024-07-10" data-position="1">
Patnic Insurance Agency v National Health <mark>Insurance</mark> Fund Management &amp; 4 others
(Constitutional Petition E015 of 2022) [2024] KEHC 8429 (KLR) (10 July 2024) (Ruling) </a></h5>
<div class="mb-2"><div><i> alt </i></div><div><span class="me-3">10 July 2024</span>
<span class="me-3">High Court</span></div></div>
<div class="ms-3 snippet text-muted">[...] the Fund failed to pay the <mark>insurance</mark> agency [...]</div>
</div></div></li>
<li class="mb-4 hit " data-document-id="2"><div class="card"><div class="card-body"><h5 class="card-title">
<a class="h5 text-primary" href="/akn/ke/bill/na/2014/13/eng@2014-03-21"> The Companies Bill, 2014 </a></h5>
<div class="mb-2"><div><span class="me-3">21 March 2014</span><span class="me-3"> National Assembly </span></div></div>
<div class="mb-1 text-muted"><a href="#">Page 1</a>: <span>[...] the <mark>levy</mark> payable [...]</span></div>
</div></div></li></ul>"""


class Resp:
    def __init__(self, status=200, json_body=None, text="", content=b""):
        self.status_code, self._json, self.text, self.content = status, json_body, text, content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_a_court_card_gives_parties_case_number_court_and_date():
    records = kenya_law_records.parse(CARD)
    case, bill = records
    assert case["kind"] == "judgment" and bill["kind"] == "bill"
    assert case["parties"] == ["Patnic Insurance Agency", "National Health Insurance Fund Management & 4 others"]
    assert case["case_number"] == "Constitutional Petition E015 of 2022"
    assert case["court"] == "High Court" and case["date"] == datetime(2024, 7, 10)
    assert case["url"] == "https://new.kenyalaw.org/akn/ke/judgment/kehc/2024/8429/eng@2024-07-10"
    assert "failed to pay the insurance agency" in case["snippet"]
    assert bill["parties"] == [] and bill["case_number"] is None, "no 'v' in the title, no parties guessed"
    m = kenya_law_records.as_mention(case)
    assert m["raw_payload"]["record"] == "primary" and m["source_type"] == "judgment"


@pytest.mark.parametrize("response,needle", [
    (Resp(403), "refused this server"),
    (Resp(200, {"count": 12, "results_html": "<ul></ul>"}), "card layout has changed"),
    (Resp(200, text="<html>"), "not JSON"),
])
def test_kenya_law_says_which_nothing(monkeypatch, response, needle):
    """A refused host, a layout the parser no longer reads, and a search
    with genuinely no result are three findings — "no court record" is a
    publishable claim and must never come from the first two."""
    monkeypatch.setattr(kenya_law_records.http, "get", lambda *a, **k: response)
    monkeypatch.setattr(kenya_law_records.http, "remember_refusal", lambda *a, **k: None)
    records, _, why = kenya_law_records.search("health insurance")
    assert records == [] and needle in why


def test_kenya_law_asks_the_way_the_api_answers(monkeypatch):
    asked = {}
    monkeypatch.setattr(kenya_law_records.http, "get",
                        lambda url, params=None, **k: asked.update(params) or Resp(200, {"count": 2, "results_html": CARD}))
    records, count, why = kenya_law_records.search("health insurance")
    assert asked == {"search": "health insurance", "page": 1}, "format=json is refused by this API"
    assert len(records) == 2 and why is None


HANSARD = """THE HANSARD
Thursday, 22nd October 2026
[The Speaker (Hon. Moses Wetang'ula) in the Chair]
Hon. Speaker: Serjeant-at-Arms, ring the Quorum Bell. Hon. Members, we now have quorum to transact business.
Hon. Jane Wanjiru (Nairobi County, UDA): Thank you, Hon. Speaker. The Social Health Authority has failed to pay facilities in my county and patients are turned away.
Hon. John Mbadi (Suba South, ODM): On roads, the contractor has not been paid for two years and the work has stopped entirely.
"""
ORDER_PAPER = """ORDER PAPER
4. Second Reading - The Social Health Authority (Amendment) Bill (National Assembly Bill No. 12 of 2026)
5. Committee of the Whole House - The Roads Bill, 2026
"""
LISTING = ('<a href="/sites/default/files/2026-10/The%20Hansard%20-%20Thursday%2C%2022%20October%202026_0.pdf">h</a>'
           '<a href="/sites/default/files/2026-12/ORDER%20PAPER%20FOR%20TUESDAY%2C%201ST%20DECEMBER%202099.pdf">o</a>'
           '<a href="/sites/default/files/2026-10/annual%20report.pdf">r</a>')


def test_parliament_keeps_the_speeches_on_the_issue_and_the_sittings_ahead(monkeypatch):
    monkeypatch.setattr(parliament_records.http, "get", lambda url, **k: Resp(200, text=LISTING))
    monkeypatch.setattr(parliament_records, "pdf_text",
                        lambda url: HANSARD if "Hansard" in url else ORDER_PAPER)
    about = lambda text: "social health authority" in text.lower()
    report: dict = {}
    speeches, ahead = parliament_records.fetch(about, datetime(2026, 8, 1), datetime(2026, 10, 31), report)
    assert [s["author_handle"] for s in speeches] == ["Hon. Jane Wanjiru (Nairobi County, UDA)"]
    assert speeches[0]["posted_at"] == datetime(2026, 10, 22) and speeches[0]["source_type"] == "hansard"
    assert speeches[0]["raw_payload"]["record"] == "primary"
    assert len(ahead) == 1 and ahead[0]["date"] == "2099-12-01" and "Social Health Authority" in ahead[0]["event"]
    assert report["documents_listed"] == 2, "an annual report is neither a Hansard nor an order paper"


def test_world_bank_reads_projects_and_documents_keyed_by_id(monkeypatch):
    def get(url, params=None, **k):
        if "projects" in url:
            return Resp(200, {"projects": {"P1": {"project_name": "Kenya Health Systems Project",
                                                   "totalamt": "500,000,000", "status": "Active",
                                                   "boardapprovaldate": "2024-05-02T00:00:00Z"}}})
        return Resp(200, {"documents": {"D1": {"display_title": "Health financing review", "docty": "Report",
                                               "docdt": "2025-01-10T00:00:00Z", "url": "https://wb.example/d1"},
                                        "facets": {}}})
    monkeypatch.setattr(world_bank_records.http, "get", get)
    p = world_bank_records.projects("health")
    d = world_bank_records.documents("health")
    assert "Kenya Health Systems Project" in p[0]["text"] and p[0]["posted_at"] == datetime(2024, 5, 2)
    assert d[0]["raw_payload"]["url"] == "https://wb.example/d1" and len(d) == 1


def test_the_map_routes_the_records_by_issue_and_one_source_down_costs_only_itself(monkeypatch):
    monkeypatch.setattr(issue_map.settings, "enable_issue_map_records", True)
    sha_card = CARD.replace("National Health <mark>Insurance</mark> Fund Management", "Social Health Authority (SHA)")
    monkeypatch.setattr(kenya_law_records, "search",
                        lambda q: (kenya_law_records.parse(sha_card) + kenya_law_records.parse(CARD)[:1], 3, None))
    monkeypatch.setattr(parliament_records, "fetch",
                        lambda about, ws, we, report: (_ for _ in ()).throw(RuntimeError("parliament down")))
    called = []
    monkeypatch.setattr(world_bank_records, "projects", lambda t: called.append(t) or [])
    monkeypatch.setattr(world_bank_records, "documents", lambda t: [])
    report: dict = {}
    mentions, ahead = issue_map.acquire_records("Ministry of Health", "SHA", None, datetime(2026, 8, 1),
                                                datetime(2026, 10, 31), ["Ministry of Health"], report)
    kinds = {m["source_type"] for m in mentions}
    assert kinds == {"judgment"}, "the 2014 bill is past the ten-year cut-off"
    assert all("Social Health Authority" in m["text"] for m in mentions)
    assert report["kenya_law"]["set_aside_off_issue"] >= 1, "the NHIF case is not the SHA record, and is counted"
    assert "parliament down" in report["parliament"]["error"]
    assert report["world_bank"]["skipped"].startswith("not a money"), "SHA is a health issue"
    assert called == []
    issue_map.acquire_records("National Treasury", "the IMF loan", None, datetime(2026, 8, 1),
                              datetime(2026, 10, 31), ["National Treasury"], {})
    assert called, "an international money issue asks the World Bank"


def test_order_papers_lead_the_dated_next_steps():
    src = open(issue_map.__file__).read()
    assert 'ahead = list((acquisition or {}).get("records_ahead") or [])' in src


def test_a_speaker_label_is_a_name_never_the_first_words_of_a_speech():
    """A live run attributed a speech to "Hon. Temporary Speaker, I wish to
    be remembered for one thin" — the label ran to a colon in the speech."""
    text = ("Hon. Temporary Speaker, I wish to be remembered for one thing: that I joined a government that "
            "built houses for every county.\n"
            "Hon. (Dr) Jane Wanjiru (Nairobi County, UDA): The Affordable Housing Levy is deducted from every "
            "payslip and the houses are not built.\n")
    speakers = [s for s, *_ in parliament_records.turns(text)]
    assert speakers == ["Hon. (Dr) Jane Wanjiru (Nairobi County, UDA)"]


def test_only_the_part_of_a_speech_about_the_issue_is_kept():
    said = ("I rise on Standing Orders. The Chair has ruled before. On another matter, the Social Health "
            "Authority has not paid hospitals in my county. Patients are turned away. Finally, roads in my "
            "ward are impassable. I thank you.")
    words = parliament_records.passage(said, lambda t: "social health authority" in t.lower())
    assert words == ("The Chair has ruled before. On another matter, the Social Health Authority has not "
                     "paid hospitals in my county. Patients are turned away."), "the sentence either side, as context"
    assert "Standing Orders" not in words and "roads" not in words
    assert parliament_records.passage(said, lambda t: "imf" in t.lower()) is None


def test_no_generic_tail_is_searched_without_its_name():
    from engine.reports import decompose

    assert "shares sale" not in decompose.decompose("Safaricom shares sale")["queries"]
    assert "funding model" in decompose.decompose("university funding model")["queries"]


# --- the page furniture is the Hansard's, not the member's --------------------

FURNISHED = """THE HANSARD
PRAYERS
COMMUNICATIONS FROM THE CHAIR
Hon. Speaker: Hon. Members, I welcome a delegation from Malawi seated in the gallery today.
22nd October 2026                      National Assembly Debates                                                  2
\x20
Disclaimer:  The electronic version of the Official Hansard Report is for information
purposes only. A certified copy of this Report can be obtained from the Hansard Editor.
THE AFFORDABLE HOUSING
BILL
Hon. Jared Okello (Nyando, ODM): This Bill takes money from every payslip. The houses are not
built in my constituency. NEPAD/KFW 2015 69 052/FW1/KEN/005 was a different matter altogether.
"""


def test_page_furniture_is_never_quoted_as_a_member_and_headings_are_the_business():
    """A live run kept "A certified copy of this Report can be obtained from
    the Hansard Editor" as the sentence before a member's remarks on the
    housing levy; and the Speaker was quoted saying a heading."""
    got = parliament_records.turns(FURNISHED)
    assert [(s, b) for s, _, b in got] == [("Hon. Speaker", "COMMUNICATIONS FROM THE CHAIR"),
                                         ("Hon. Jared Okello (Nyando, ODM)", "THE AFFORDABLE HOUSING BILL")]
    said = " ".join(t for _, t, _ in got)
    assert "certified copy" not in said and "Debates" not in said and "AFFORDABLE" not in said
    assert "NEPAD/KFW" in said, "a reference quoted in a speech is not a heading"


def test_a_speech_under_a_heading_naming_the_issue_is_kept_though_it_never_repeats_the_name(monkeypatch):
    from engine.reports import issue_match

    monkeypatch.setattr(parliament_records.http, "get", lambda url, **k: Resp(200, text=LISTING))
    monkeypatch.setattr(parliament_records, "pdf_text", lambda url: FURNISHED if "Hansard" in url else "")
    speeches, _ = parliament_records.fetch(issue_match.of("Affordable Housing Bill"), datetime(2026, 8, 1),
                                           datetime(2026, 10, 31), {})
    assert len(speeches) == 1 and speeches[0]["text"].startswith("This Bill takes money")
    assert speeches[0]["raw_payload"]["business"] == "THE AFFORDABLE HOUSING BILL"


# --- a court card is the record of the issue, or it is set aside, and why -----

def _card(title, snippet, uri="/akn/ke/judgment/kehc/2025/1/eng@2025-01-10"):
    return (f'<li class="mb-4 hit "><div class="card"><div class="card-body"><h5 class="card-title">'
            f'<a class="h5" href="{uri}">{title}</a></h5><div class="mb-2"><div><span class="me-3">'
            f'10 January 2025</span><span class="me-3">High Court</span></div></div>'
            f'<div class="ms-3 snippet text-muted">{snippet}</div></div></div></li>')


@pytest.mark.parametrize("issue,title,snippet,reason", [
    ("SHA", "JMA v SHA [2025] KEHC 6552 (KLR)", "APPELLANT VERSUS SHA", "initials"),
    ("SHA", "Abdille v Social Health Authority (SHA) & 3 others (Petition E078 of 2025) [2025] KEELRC 2027 (KLR)",
     "the Authority (SHA)", None),
    ("school fees", "MNN v JMM (Miscellaneous Application E024 of 2021) [2025] KEHC 17634 (KLR) (Family) (Ruling)",
     "The school fees balance", "family"),
    ("school fees", "Masese v Nyarenchi (Civil Appeal E058 of 2023) [2025] KEHC 8320 (KLR) (Ruling)",
     "commitment to pay school fees", "private"),
    ("school fees", "Okoiti v Cabinet Secretary for Education & 2 others (Petition E1 of 2024) [2025] KEHC 1 (KLR)",
     "capitation and school fees in day secondary schools", None),
    ("Safaricom shares sale", "In re Estate of Eutycus Muthui (Deceased) [2025] KEHC 3728 (KLR)",
     "include the SAFARICOM shares", "family"),
    ("Safaricom shares sale", "Amana Capital Limited v Capital Markets Authority & another (Appeal 2 of 2019) "
     "[2025] KECMT 6 (KLR)", "Company, Super Shares Limited", "issue"),
    ("the IMF loan", "Okoiti & 8 others v H. E. (Former) President Uhuru Muigai Kenyatta & 33 others "
     "(Petition E216 of 2025) [2025] KEHC 9055 (KLR)", "loans from the IMF were contracted", None),
])
def test_a_court_card_stays_only_as_the_record_of_the_issue(monkeypatch, issue, title, snippet, reason):
    """Each set-aside from the first live run, by its own reason — and the
    record that belongs, kept."""
    monkeypatch.setattr(issue_map.settings, "enable_issue_map_records", True)
    monkeypatch.setattr(kenya_law_records, "search", lambda q: (kenya_law_records.parse(_card(title, snippet)), 1, None))
    monkeypatch.setattr(parliament_records, "fetch", lambda about, ws, we, report: ([], []))
    monkeypatch.setattr(world_bank_records, "projects", lambda t: [])
    monkeypatch.setattr(world_bank_records, "documents", lambda t: [])
    report: dict = {}
    mentions, _ = issue_map.acquire_records("Kenya Government", issue, None, datetime(2026, 8, 1),
                                            datetime(2026, 10, 31), ["Kenya Government"], report)
    aside = report["kenya_law"]["set_aside"]
    if reason is None:
        assert len(mentions) == 1 and not aside
    else:
        assert not mentions and len(aside) == 1 and reason in next(iter(aside)), aside


def test_world_bank_titles_lose_the_apis_line_breaks(monkeypatch):
    monkeypatch.setattr(world_bank_records.http, "get", lambda url, params=None, **k: Resp(200, {"documents": {
        "D1": {"display_title": "Kenya - Joint World Bank-IMF Debt\n            Sustainability Analysis",
               "docty": "Board Report", "docdt": "2022-01-01T00:00:00Z"}}}))
    assert world_bank_records.documents("IMF")[0]["text"] == \
        "Board Report — Kenya - Joint World Bank-IMF Debt Sustainability Analysis"
