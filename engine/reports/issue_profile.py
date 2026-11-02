"""What kind of issue this is, and therefore where its record lives.

An Issue Map is asked about anything — SHA, school fees, a teachers'
strike, the housing levy, a court case, a tender, a bill, a forest
eviction — and the map treated every one the same way: one search phrase,
and the same five research angles ("court", "parliament", "ruling",
"petition", "audit") whatever the issue was. A teachers' strike is decided
by the TSC, KNUT and KUPPET; SHA by the Ministry of Health, the Authority's
board and the Health committee; a forest eviction by the Kenya Forest
Service, the National Land Commission and the Environment and Land Court.
A search that does not name the bodies that decide an issue finds the
commentary on it and misses the record.

This reads the issue with no model, deterministically, and returns:

  * `expansions` — what the issue is also called: an acronym's full name
    (SHA is the Social Health Authority, and "SHA" alone also finds
    SHA-256 and Sha'Carri Richardson), and the Kenyan names a programme
    goes by ("housing levy" is the Affordable Housing Levy).
  * `kinds` — legal, legislation, public money, health, education,
    security and rights, land and environment, elections, devolution,
    energy, transport, agriculture, corporate, international. More than one
    can hold.
  * `institutions` — the bodies with formal power over issues of those
    kinds, as a source prints them, for the map's research probes.
  * `records` — which primary-record sources an issue of those kinds is
    made on (court records, Hansard, the Gazette, procurement, the
    international lenders), for the connectors of
    `docs/ISSUE_MAP_FOUNDATION.md` Phase 1 to be asked first.
  * `search_terms` and `identities` — what to put in a search box, and what
    a document must mention to count as being about the issue.

The lexicon is curated, small and checked: an expansion that is wrong
sends every search after the wrong thing, so an acronym is listed only
where its Kenyan meaning is the one a Kenyan issue map means.
"""

from __future__ import annotations

import re

from engine.reports import decompose

#: Acronym -> what Kenyan sources also call it. Kenyan meaning only.
ACRONYMS: dict[str, tuple[str, ...]] = {
    "SHA": ("Social Health Authority",),
    "SHIF": ("Social Health Insurance Fund",),
    "NHIF": ("National Hospital Insurance Fund",),
    "CBC": ("Competency Based Curriculum",),
    "CBE": ("Competency Based Education",),
    "KUCCPS": ("Kenya Universities and Colleges Central Placement Service",),
    "HELB": ("Higher Education Loans Board",),
    "TSC": ("Teachers Service Commission",),
    "KNEC": ("Kenya National Examinations Council",),
    "KNUT": ("Kenya National Union of Teachers",),
    "KUPPET": ("Kenya Union of Post Primary Education Teachers",),
    "UASU": ("Universities Academic Staff Union",),
    "KMPDU": ("Kenya Medical Practitioners, Pharmacists and Dentists Union",),
    "KMTC": ("Kenya Medical Training College",),
    "KEMSA": ("Kenya Medical Supplies Authority",),
    "KRA": ("Kenya Revenue Authority",),
    "EACC": ("Ethics and Anti-Corruption Commission",),
    "IEBC": ("Independent Electoral and Boundaries Commission",),
    "DCI": ("Directorate of Criminal Investigations",),
    "ODPP": ("Director of Public Prosecutions",),
    "IPOA": ("Independent Policing Oversight Authority",),
    "KNCHR": ("Kenya National Commission on Human Rights",),
    "NCIC": ("National Cohesion and Integration Commission",),
    "LSK": ("Law Society of Kenya",),
    "NPS": ("National Police Service",),
    "KDF": ("Kenya Defence Forces",),
    "NYS": ("National Youth Service",),
    "KPLC": ("Kenya Power",),
    "KENGEN": ("Kenya Electricity Generating Company",),
    "EPRA": ("Energy and Petroleum Regulatory Authority",),
    "SGR": ("Standard Gauge Railway",),
    "JKIA": ("Jomo Kenyatta International Airport",),
    "KAA": ("Kenya Airports Authority",),
    "KENHA": ("Kenya National Highways Authority",),
    "NTSA": ("National Transport and Safety Authority",),
    "NLC": ("National Land Commission",),
    "KFS": ("Kenya Forest Service",),
    "KWS": ("Kenya Wildlife Service",),
    "NEMA": ("National Environment Management Authority",),
    "NCPB": ("National Cereals and Produce Board",),
    "KEBS": ("Kenya Bureau of Standards",),
    "CBK": ("Central Bank of Kenya",),
    "CMA": ("Capital Markets Authority",),
    "NSE": ("Nairobi Securities Exchange",),
    "PPRA": ("Public Procurement Regulatory Authority",),
    "SRC": ("Salaries and Remuneration Commission",),
    "CRA": ("Commission on Revenue Allocation",),
    "PSC": ("Public Service Commission",),
    "NHC": ("National Housing Corporation",),
    "KCSE": ("Kenya Certificate of Secondary Education",),
    "KCPE": ("Kenya Certificate of Primary Education",),
    "IMF": ("International Monetary Fund",),
    "ICC": ("International Criminal Court",),
    "MSS": ("Multinational Security Support",),
}

#: An issue phrase -> the names Kenyan sources print for it. Matched on
#: the folded issue text; each value was checked against how the press
#: names the thing.
PHRASES: dict[str, tuple[str, ...]] = {
    "housing levy": ("Affordable Housing Levy", "Affordable Housing Programme", "Affordable Housing Act"),
    "affordable housing": ("Affordable Housing Levy", "Affordable Housing Programme", "Affordable Housing Act"),
    "haiti mission": ("Multinational Security Support", "Kenyan police in Haiti"),
    "haiti deployment": ("Multinational Security Support", "Kenyan police in Haiti"),
    "university funding model": ("new university funding model", "higher education funding model"),
    "school fees": ("capitation",),
    "school capitation": ("capitation",),
    "gen z protests": ("Gen Z", "Finance Bill protests"),
    "finance bill protests": ("Gen Z", "Finance Bill protests"),
    "teachers strike": ("teachers' strike",),
    "doctors strike": ("doctors' strike",),
    "abductions": ("enforced disappearances",),
    "e-citizen": ("eCitizen",),
    "hustler fund": ("Financial Inclusion Fund",),
    "fertiliser subsidy": ("fertiliser subsidy programme", "subsidised fertiliser"),
    "fertilizer subsidy": ("fertiliser subsidy programme", "subsidised fertiliser"),
}

#: kind -> (trigger words in the issue or its expansions, institutions a
#: source prints, primary-record sources). Triggers are whole words.
KINDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "legal": (("case", "court", "petition", "ruling", "judgment", "judgement", "lawsuit", "suit",
               "appeal", "injunction", "nullified", "nullify", "unconstitutional", "conservatory",
               "tribunal", "prosecution", "charged", "trial", "acquitted"),
              ("High Court", "Court of Appeal", "Supreme Court", "Attorney General", "Law Society of Kenya"),
              ("kenya_law", "cause_lists")),
    "legislation": (("bill", "act", "law", "amendment", "regulations", "policy", "motion", "statute",
                     "constitution", "referendum"),
                    ("National Assembly", "Senate", "parliamentary committee", "public participation"),
                    ("hansard", "gazette")),
    "public_money": (("tender", "contract", "procurement", "loan", "debt", "eurobond", "budget",
                      "allocation", "allocations", "levy", "tax", "taxes", "subsidy", "funding", "fund",
                      "revenue", "expenditure", "audit", "bailout", "privatisation", "privatization",
                      "sale", "shares", "stake", "deal", "fees", "capitation"),
                     ("National Treasury", "Auditor-General", "Controller of Budget", "Public Accounts Committee"),
                     ("ppip", "gazette", "world_bank")),
    "health": (("health", "hospital", "hospitals", "doctors", "nurses", "clinics", "medical", "sha", "shif",
                "nhif", "kemsa", "kmpdu", "disease", "outbreak", "insurance"),
               ("Ministry of Health", "Social Health Authority", "KMPDU", "Health committee"),
               ("hansard", "gazette", "ppip")),
    "education": (("school", "schools", "university", "universities", "teachers", "students", "cbc", "cbe",
                   "kuccps", "helb", "tsc", "knec", "knut", "kuppet", "uasu", "exams", "examination",
                   "kcse", "kcpe", "curriculum", "capitation", "fees", "education"),
                  ("Ministry of Education", "Teachers Service Commission", "KNUT", "KUPPET", "Education committee"),
                  ("hansard", "gazette")),
    "security_rights": (("police", "abductions", "abduction", "disappearances", "protests", "protest",
                         "killings", "brutality", "ipoa", "dci", "terror", "terrorism", "security", "kdf",
                         "detention", "massacre", "extrajudicial"),
                        ("National Police Service", "IPOA", "KNCHR", "Law Society of Kenya", "DCI"),
                        ("kenya_law", "hansard")),
    "land_environment": (("land", "forest", "evictions", "eviction", "title", "titles", "grabbing", "nlc",
                          "kfs", "kws", "nema", "environment", "climate", "wildlife", "mining", "water"),
                         ("National Land Commission", "Kenya Forest Service", "Environment and Land Court", "NEMA"),
                         ("kenya_law", "gazette")),
    "elections": (("election", "elections", "iebc", "by-election", "nomination", "party", "coalition",
                   "vote", "voting", "boundaries", "campaign"),
                  ("IEBC", "Registrar of Political Parties", "Political Parties Disputes Tribunal"),
                  ("kenya_law", "gazette")),
    "energy": (("power", "electricity", "blackouts", "blackout", "kplc", "kengen", "epra", "fuel", "oil",
                "pipeline", "dam", "geothermal", "tariffs"),
               ("Ministry of Energy", "EPRA", "Kenya Power", "KenGen"),
               ("ppip", "gazette", "world_bank")),
    "transport": (("sgr", "railway", "road", "roads", "airport", "jkia", "port", "expressway", "matatu",
                   "ntsa", "aviation", "airline"),
                  ("Ministry of Roads and Transport", "Kenya Airports Authority",
                   "Kenya National Highways Authority", "Kenya Ports Authority"),
                  ("ppip", "gazette", "world_bank")),
    "agriculture": (("maize", "fertiliser", "fertilizer", "tea", "coffee", "sugar", "milk", "dairy", "farmers",
                     "ncpb", "cereals", "food", "prices", "harvest", "livestock"),
                    ("Ministry of Agriculture", "NCPB", "Agriculture committee", "AFA"),
                    ("gazette", "hansard")),
    "devolution": (("county", "counties", "governor", "governors", "devolution", "mca", "mcas", "ward",
                    "equitable", "allocations"),
                   ("Council of Governors", "Commission on Revenue Allocation", "Senate", "Controller of Budget"),
                   ("gazette", "hansard")),
    "corporate": (("shares", "stake", "company", "listing", "nse", "merger", "acquisition", "shareholders",
                   "dividend", "ipo", "privatisation", "privatization"),
                  ("Capital Markets Authority", "Nairobi Securities Exchange", "Competition Authority of Kenya"),
                  ("gazette", "icij")),
    "international": (("imf", "world", "bank", "icc", "eurobond", "haiti", "mss", "diaspora", "un", "au",
                       "treaty", "embassy", "foreign"),
                      ("International Monetary Fund", "World Bank", "Ministry of Foreign Affairs"),
                      ("world_bank",)),
}

#: Most specific first: when an issue is several kinds at once ("school
#: fees" is education AND public money), the bodies of the specific kind
#: lead, and the institutions are taken in turn from each kind so none
#: crowds the others out. Public money and legislation come last because
#: nearly every issue touches them.
PRIORITY = ("legal", "health", "education", "security_rights", "land_environment", "elections",
            "devolution", "energy", "transport", "agriculture", "corporate", "international",
            "legislation", "public_money")

MAX_INSTITUTIONS = 6
MAX_SEARCH_TERMS = 7


def _folded(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9\-]+", (text or "").lower()))


def expansions(issue: str) -> list[str]:
    """What the issue is also called — its acronyms spelled out, and the
    names Kenyan sources print for it."""
    out: list[str] = []
    for acronym in decompose.acronyms(issue):
        out.extend(ACRONYMS.get(acronym.upper(), ()))
    folded = _folded(issue)
    for phrase, names in PHRASES.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
            out.extend(names)
    return decompose._dedupe(out)


def kinds(issue: str, extra: list[str] | None = None) -> list[str]:
    words = set(_folded(" ".join([issue, *(extra or [])])).split())
    return [kind for kind in PRIORITY if words & set(KINDS[kind][0])]


def _in_turn(lists: list[tuple[str, ...]], most: int) -> list[str]:
    out: list[str] = []
    for i in range(max((len(x) for x in lists), default=0)):
        for items in lists:
            if i < len(items) and items[i] not in out:
                out.append(items[i])
    return out[:most]


def profile(issue: str, aliases: list[str] | None = None) -> dict:
    """Everything the map needs to know about the issue, with no model."""
    issue = (issue or "").strip()
    parts = decompose.decompose(issue)
    more = expansions(issue)
    found = kinds(issue, more)
    institutions = _in_turn([KINDS[k][1] for k in found], MAX_INSTITUTIONS)
    records = decompose._dedupe([r for k in found for r in KINDS[k][2]])
    typed = [a for a in aliases or [] if a and a.strip()]
    # The typed issue's own search terms first, then what it is also
    # called, then the operator's aliases. An acronym's expansion goes
    # straight after the acronym: "SHA" alone also finds SHA-256.
    terms = decompose._dedupe([*parts["queries"][:3], *more, *parts["queries"][3:], *typed])
    identities = decompose._dedupe([*parts["identities"], *more, *typed])
    return {"issue": issue, "kinds": found, "expansions": more, "institutions": institutions,
            "records": records, "search_terms": terms[:MAX_SEARCH_TERMS], "identities": identities,
            "anchors": parts["anchors"], "sentence": parts["sentence"]}
