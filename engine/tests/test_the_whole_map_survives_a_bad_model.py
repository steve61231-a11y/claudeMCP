"""The whole Issue Map, end to end, when the model fails in every way it can.

Every piece of the map has its own tests, each with the pieces around it
stubbed. Nothing ran the map WHOLE — the records, the fact store, the
connection, the verdict, the scenarios, the framework, the changes since the
last map — and then put the payload on the page, so a fault between two
pieces could only show itself on a live run, in front of a client. The map
had not completed a live run since its five phases landed (no model credit).

So this drives the real `build_issue_map` over a realistic corpus — news
items, a court card, a Hansard speech, a cause-list listing ahead — with the
model in each of the states a live run meets: down, answering nothing,
answering the wrong JSON shape, and answering junk for every key. The map
must finish, say what failed, carry nothing the page cannot draw, and the
page must draw it without an error and without printing "undefined", "NaN",
"null" or "[object Object]" to a reader.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from engine.tests.test_the_framework_stays_where_the_reader_left_it import CHROME, _server

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

PRINCIPAL, ISSUE = "National Treasury", "IMF loan"
WE = datetime(2026, 10, 31)
WS = WE - timedelta(days=90)


class DeepJunk(dict):
    """Lists of objects whose every field is the wrong kind, nested."""

    def __init__(self, depth=0):
        super().__init__({"records": [], "sentences": []})
        self._i, self._depth = 0, depth

    def _next(self):
        self._i += 1
        options = ["", 7, None, "x", 1.5, True, ["a", 2, None]]
        if self._depth < 2:
            options += [[DeepJunk(self._depth + 1), DeepJunk(self._depth + 1)], DeepJunk(self._depth + 1)]
        return options[self._i % len(options)]

    def get(self, key, default=None):
        return self._next()

    def __getitem__(self, key):
        return self._next()

    def __contains__(self, key):
        return True


class JunkItem(dict):
    """An object in the right place whose fields are each the wrong kind."""

    def __init__(self, seed=0):
        super().__init__({"label": None})
        self._i = seed

    def _next(self):
        self._i += 1
        return ["", 7, None, 1.5, True, ["a", 2, None], {"x": None}, "x", -3, 250][self._i % 10]

    def get(self, key, default=None):
        return self._next()

    def __getitem__(self, key):
        return self._next()

    def __contains__(self, key):
        return True


class Items(dict):
    """A reply whose every key is a list of JunkItems."""

    def __init__(self):
        super().__init__({"records": []})
        self._i = 0

    def get(self, key, default=None):
        self._i += 1
        return [JunkItem(self._i + n) for n in range(4)]

    def __getitem__(self, key):
        return self.get(key)

    def __contains__(self, key):
        return True


class Junk(dict):
    """A model reply that is a dict and answers every key with a wrong type."""

    VALUES = ["junk", 7, None, [None, "x", 3], {"x": None}]

    def __init__(self):
        super().__init__({"records": "junk", "verdict": 7})
        self._i = 0

    def _next(self):
        self._i += 1
        value = self.VALUES[self._i % len(self.VALUES)]
        return list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value

    def get(self, key, default=None):
        return self._next()

    def __getitem__(self, key):
        return self._next()

    def __contains__(self, key):
        return True


def _honest_extractor(prompt, untrusted="", *a, **k):
    """What a working fact extractor returns: one record per item naming the
    principal, its statement the item's own sentence, its quote its words —
    so the verified record exists and the WRITERS above it can be tested."""
    import re

    records = []
    for m in re.finditer(r"\[(\d+)\] platform=[^\n]*\n([^\[]+)", untrusted or ""):
        body = " ".join(m.group(2).split())
        if "Treasury" in body:
            sentence = body.split(". ")[0].rstrip(".") + "."
            records.append({"i": int(m.group(1)), "kind": "fact", "status": "reported", "statement": sentence,
                            "topic": "IMF loan", "actor": "", "sentiment": "neutral",
                            "quote": " ".join(body.split()[:8])})
    return {"records": records}


def _facts_then(writer):
    def call(prompt, *a, **k):
        if "monitoring items about" in prompt:
            return _honest_extractor(prompt, *a, **k)
        return writer(prompt, *a, **k)
    return call


MODES = {
    "down": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider unavailable: HTTP 503")),
    "empty": lambda *a, **k: {},
    "wrong_shape": lambda *a, **k: [],
    "junk": lambda *a, **k: Junk(),
    "deep_junk": lambda *a, **k: DeepJunk(),
    "junk_items": lambda *a, **k: Items(),
}
#: The verified record is read, and the writers over it fail.
WRITER_MODES = {f"facts_then_{name}": _facts_then(fn) for name, fn in MODES.items()}
MODES.update(WRITER_MODES)


def _corpus():
    text = [
        "The National Treasury signed a new IMF loan programme worth 850 million dollars, the Daily Nation reported.",
        "Treasury officials defended the IMF loan conditions before the Finance committee on Tuesday.",
        "Okiya Omtatah filed a petition against the National Treasury over the IMF loan terms.",
        "Protesters marched in Nairobi against tax measures tied to the IMF loan.",
        "The IMF said its board would review Kenya's programme on 15 December 2026.",
    ]
    return [{"id": f"m-{i}", "kind": "mention", "platform": "nation.africa", "source_type": "article",
             "author_handle": "Daily Nation", "text": t, "posted_at": WS + timedelta(days=10 + i * 7),
             "engagement": {}, "language": "en", "source_url": f"https://nation.africa/kenya/news/{i}",
             "date_estimated": False, "full_text": True, "evidence_pool": "core"}
            for i, t in enumerate(text)]


def _records():
    from engine.ingestion import kenya_law_records

    card = {"title": "Omtatah v National Treasury & 2 others (Petition E181 of 2023) [2024] KEHC 1 (KLR)",
            "url": "https://new.kenyalaw.org/akn/ke/judgment/kehc/2024/1/eng@2024-05-02", "kind": "judgment",
            "court": "High Court", "court_code": "kehc", "date": datetime(2024, 5, 2),
            "parties": kenya_law_records.parties(
                "Omtatah v National Treasury & 2 others (Petition E181 of 2023) [2024] KEHC 1 (KLR)"),
            "case_number": "Petition E181 of 2023",
            "snippet": "the petition challenges the IMF loan agreement entered into by the National Treasury"}
    court = kenya_law_records.as_mention(card)
    hansard = {"platform": "parliament.go.ke", "source_type": "hansard", "author_handle": "Hon. Kimani Ichung'wah",
               "text": "The IMF loan programme will be tabled before this House for approval.",
               "posted_at": WE - timedelta(days=5), "engagement": {},
               "raw_payload": {"url": "https://www.parliament.go.ke/hansard/1.pdf", "sitting": "2026-10-26",
                               "business": "IMF LOAN PROGRAMME", "record": "primary"}}
    ahead = [{"date": "2026-12-15", "event": "Petition E181 of 2023 is listed before the High Court",
              "publisher": "Kenya Law — Daily Cause List", "url": "https://new.kenyalaw.org/akn/ke/doc/x",
              "printed": None, "basis": "stated_in_source", "record": "cause_list"}]
    return [court, hansard], ahead


@pytest.fixture
def world(db_session, monkeypatch):
    import engine.db.session as session_mod
    from engine.db.models import Politician
    from engine.reports import issue_map

    subject = Politician(name=PRINCIPAL, aliases=["Treasury"], subject_type="institution")
    db_session.add(subject)
    db_session.commit()
    monkeypatch.setattr(session_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    items, ahead = _records()
    from engine.db.models import RawMention

    news = _corpus()
    for i, item in enumerate(news):
        row = RawMention(politician_id=subject.id, platform=item["platform"], source_type="article",
                         author_handle=item["author_handle"], text=item["text"], posted_at=item["posted_at"],
                         engagement_json={}, raw_payload={"url": item["source_url"]},
                         content_hash=f"whole-map-{i}", is_spam=0)
        db_session.add(row)
        db_session.flush()
        item["id"] = str(row.id)
    db_session.commit()
    corpus = news + issue_map._as_corpus(items)

    def acquire(principal, issue, ws, we, aliases, publish):
        return list(corpus), {
            "stored": True, "subject_id": subject.id, "issue_terms": ["IMF loan", "IMF"],
            "records": {"kenya_law": {"records": 1}, "parliament": {"speeches": 1}, "world_bank": {"items": 0},
                        "total": 2},
            "records_ahead": ahead, "record_items": items, "identities": [PRINCIPAL, "Treasury"],
            "relevance_filter": {"examined": len(corpus), "kept": len(corpus),
                                 "pools": {"core": 5, "principal": 0, "issue": 0}, "dropped": 0}}

    monkeypatch.setattr(issue_map, "_acquire_and_store", acquire)
    return db_session


def _run(mode, monkeypatch):
    from engine import llm
    from engine.reports import issue_map, map_changes

    monkeypatch.setattr(llm, "call_json", MODES[mode])
    monkeypatch.setattr(llm, "call_json_untrusted", MODES[mode])
    llm.reset_breaker()
    payload = issue_map.build_issue_map(PRINCIPAL, ISSUE, WS, WE, desired_outcome="The loan is approved")
    payload["changes"] = map_changes.compare(json.loads(json.dumps(payload, default=str)), payload)
    return json.loads(json.dumps(payload, default=str))


RENDER = """(m) => {
  const out = document.createElement('div'); out.id = 'map';
  document.querySelector('#zenith').appendChild(out);
  window.ZENITH.renderIssue(out, m);
  out.querySelectorAll('details').forEach(d => d.open = true);
  out.querySelectorAll('.sect').forEach(s => s.setAttribute('open', ''));
  return out.innerText;
}"""


@pytest.mark.parametrize("mode", list(MODES))
def test_the_map_finishes_and_draws_whatever_the_model_does(mode, world, monkeypatch):
    from engine.api_server import render_frontend_document

    payload = _run(mode, monkeypatch)
    # The record's answer does not depend on the model: the court role stands.
    paths = payload.get("connection_paths") or {}
    assert paths.get("state") == "measured", paths
    assert (paths.get("counts") or {}).get("court") == 1, paths.get("lead")
    if mode.startswith("facts_then_"):
        # The writers over the record were reached, and each failure says why
        # in words — never a bare Python error from reading the wrong type.
        for section in ("verdict_from_record", "scenarios"):
            reason = str((payload.get(section) or {}).get("reason") or "")
            assert "object is not" not in reason and "has no attribute" not in reason, (section, reason)
        record = payload.get("connection_record") or {}
        assert record.get("state") == "measured" and (record.get("counts") or {}).get("findings"), \
            f"the honest extractor's facts did not reach the connection: {record}"

    server = _server(render_frontend_document())
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            page.wait_for_function("() => window.ZENITH && window.ZENITH.renderIssue")
            text = page.evaluate(RENDER, payload)
            browser.close()
    finally:
        server.shutdown()
    assert not errors, errors
    for bad in ("undefined", "NaN", "[object Object]"):
        assert bad not in text, f"{bad!r} reached the reader: …{text[max(0, text.find(bad) - 160):text.find(bad) + 80]}…"
    import re
    m = re.search(r"(?<![\w-])null(?![\w-])", text)
    assert not m, f"'null' reached the reader: …{text[max(0, m.start() - 160):m.start() + 80]}…"
