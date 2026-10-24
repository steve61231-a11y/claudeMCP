"""The Issue Map's connection, on the record — counted, with no model writing it.

An Issue Map answers "how is this person connected to this issue?", and its
answer was a verdict a model wrote over a digest. The founder's standard —
"if we say this was said, or Martha Karua did this or that, it has to be
legit data" — is about exactly that sentence: in a client's file, "X is tied
to the IMF loan" is an allegation unless something on the record says it.

Now the map opens with every verified statement naming both the principal
and the issue: the sources' words, independent origins counted, allegations
kept as allegations, dated by first publication. Its lead line is
arithmetic. "Not established on the record" is said in words; a record that
could not be read is the operator's, never "no connection".
"""

from datetime import datetime
from pathlib import Path

import pytest

from engine.evidence import connection, facts


@pytest.mark.parametrize("statement,issue,aliases,expected", [
    ("Omtatah petitioned the court over the IMF loan conditions.", "IMF", [], True),
    ("Omtatah criticised the imfamous decision.", "IMF", [], False),
    ("Omtatah filed the odious debt petition in the High Court.", "Odious debt case", [], True),
    ("Omtatah filed a new case against the Senate.", "Odious debt case", [], False),
    ("Omtatah spoke on the International Monetary Fund programme.", "IMF",
     ["International Monetary Fund"], True),
])
def test_a_statement_names_the_issue_by_its_phrase_or_its_distinctive_words(statement, issue, aliases, expected):
    assert connection.about_issue(statement, issue, aliases) is expected


def _finding(statement, status="reported", origins=1, source_type="article", publisher="Daily Nation",
             day="2026-10-10"):
    return {"statement": statement, "status": status, "independent_sources": origins,
            "first_seen": day, "last_seen": day, "publishers": 1,
            "sources": [{"publisher": publisher, "ref": "aaaa1111", "url": "https://n.example",
                         "quote": statement.lower(), "source_type": source_type, "published_at": day}]}


FINDINGS = [
    _finding("Okiya Omtatah petitioned the High Court over the IMF loan.", origins=3, day="2026-10-02"),
    _finding("Okiya Omtatah alleged the IMF loan was signed without Parliament.", status="alleged",
             publisher="The Star", day="2026-10-05"),
    _finding("Okiya Omtatah's petition on the IMF loan was listed for hearing.", source_type="judgment",
             publisher="Kenya Law", day="2026-10-09"),
    _finding("Okiya Omtatah toured Busia county.", day="2026-10-07"),
]


def test_only_statements_naming_both_are_the_connection_and_the_lead_is_counted():
    out = connection.build(FINDINGS, "Okiya Omtatah", "IMF")
    assert out["state"] == "measured"
    assert out["findings_total"] == 3 and out["principal_findings"] == 4
    assert out["counts"] == {"findings": 3, "established": 2, "corroborated": 1, "alleged": 1,
                             "primary": 1, "publishers": 3}
    assert out["lead"] == ("3 verified statements on the record name both Okiya Omtatah and IMF, "
                           "printed by 3 outlets: 1 independently corroborated; 1 alleged, not "
                           "established; 1 resting on an official record.")
    assert [t["date"] for t in out["timeline"]] == ["2026-10-02", "2026-10-05", "2026-10-09"]


def test_every_number_in_the_lead_was_counted():
    import re

    out = connection.build(FINDINGS, "Okiya Omtatah", "IMF")
    stated = {int(n) for n in re.findall(r"\b\d+\b", out["lead"])}
    assert stated <= set(out["counts"].values())


def test_no_connection_on_the_record_is_said_in_words():
    out = connection.build(FINDINGS[3:], "Okiya Omtatah", "IMF")
    assert out["findings"] == []
    assert "not established on the record" in out["lead"]
    assert "1 finding(s) about Okiya Omtatah" in out["lead"]


def test_a_record_that_could_not_be_read_is_not_no_connection():
    out = connection.not_measured("this map's sources were not stored")
    assert out["state"] == "not_measured" and "lead" not in out


def test_a_map_built_from_unstored_material_says_why_it_has_no_record(monkeypatch):
    from engine.reports import analysts as analysts_mod
    from engine.reports import digest as digest_mod
    from engine.reports import issue_map as im
    from engine.tests.test_issue_map import _mentions

    monkeypatch.setattr(digest_mod.llm, "call_json", lambda *a, **k: {"digest": {"themes": []}})
    monkeypatch.setattr(analysts_mod.llm, "call_json", lambda *a, **k: {"verdict": "central actor"})
    out = im.build_issue_map("William Ruto", "forestry", mentions=_mentions(20))
    assert out["connection_record"] == {"state": "not_measured",
                                        "reason": "this map's sources were not stored, so they "
                                                  "could not be read for the record"}


def test_the_map_reads_its_stored_sources_for_the_connection(db_session, monkeypatch):
    from engine import llm
    from engine.db.models import Politician, RawMention
    from engine.reports import issue_map as im

    subject = Politician(name="Okiya Omtatah", aliases=["Omtatah"])
    db_session.add(subject)
    db_session.flush()
    text = ("Okiya Omtatah petitioned the High Court over the IMF loan conditions on Tuesday. "
            "Okiya Omtatah also toured Busia county.")
    mention = RawMention(politician_id=subject.id, platform="nation.africa", source_type="article",
                         author_handle="Daily Nation", text=text, posted_at=datetime(2026, 10, 2),
                         engagement_json={}, raw_payload={}, content_hash="omtatah-imf", is_spam=0)
    db_session.add(mention)
    db_session.commit()
    corpus = [{"id": mention.id, "kind": "mention", "platform": "nation.africa", "source_type": "article",
               "author_handle": "Daily Nation", "text": text, "full_text": True,
               "posted_at": datetime(2026, 10, 2), "date_estimated": False}]
    records = [{"i": 1, "kind": "fact", "status": "reported",
                "statement": "Okiya Omtatah petitioned the High Court over the IMF loan conditions.",
                "quote": "petitioned the High Court over the IMF loan conditions"},
               {"i": 1, "kind": "fact", "status": "reported",
                "statement": "Okiya Omtatah toured Busia county.", "quote": "also toured Busia county"}]
    monkeypatch.setattr(llm, "call_json_untrusted", lambda *a, **k: {"records": records})
    import engine.db.session as session_mod

    monkeypatch.setattr(session_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    job = im._start_connection_reading({"subject_id": subject.id}, "Okiya Omtatah", corpus)
    out = im._finish_connection(job, "Okiya Omtatah", "IMF", [], corpus)
    assert out["state"] == "measured"
    assert [f["statement"] for f in out["findings"]] == [records[0]["statement"]]
    assert out["principal_findings"] == 2
    assert out["findings"][0]["sources"][0]["quote"] == records[0]["quote"]


def test_search_demand_asks_the_pair_together(monkeypatch):
    from engine.config import settings
    from engine.evidence import search_demand
    from engine.reports import issue_map as im

    asked = {}
    monkeypatch.setattr(settings, "enable_search_demand", True)
    monkeypatch.setattr(search_demand, "gather",
                        lambda *a, **k: asked.update(k) or {"KE": {}, "": {}, "suggestions": {}, "wikipedia": {}})
    future = im._start_search_demand("Okiya Omtatah", "IMF", datetime(2026, 1, 1), datetime(2026, 10, 24))
    out = im._finish_search_demand(future, "Okiya Omtatah", datetime(2026, 1, 1), datetime(2026, 10, 24), [], [])
    assert asked["keywords"] == ["Okiya Omtatah", "IMF"], "IMF is three letters; it must not be dropped"
    assert out["state"] == "not_measured"


def test_the_page_opens_the_map_with_the_connection_and_keeps_failures_for_the_operator():
    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    at = html.index("function renderConnection(")
    body = html[at:at + 2600]
    assert "c.classList.add('op-only')" in body
    assert "recordRow(f)" in body
    issue = html[html.index("function renderIssue(out,m){"):]
    assert issue.index("renderConnection(m.connection_record,m)") < issue.index("const modes=[]"), \
        "the connection is the map's first answer, above the tabs"
    assert "Not established on the record" in issue[:6000]
