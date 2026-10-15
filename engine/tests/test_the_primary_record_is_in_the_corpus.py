"""The report read journalists writing about gazette notices, not the notices.

A live report's biggest storyline was "Ruto board appointments" — sixty-nine
news items about appointments to EPRA, LAPSSET and the TSC. Every one of
those appointments is a notice in the Kenya Gazette, and the corpus held
none. The same report carried the headline "High Court rules presidential
advisory offices and appointments unconstitutional" and not the judgment.

That is what "this report is too short" was actually about. It is not
volume: four keyword sources asked more questions return more coverage of
events. The documents the events are reported FROM were absent by
construction.

It is also what the product is for. "The Standard says he appointed X" is a
press summary a client could assemble themselves. "Gazette Notice No. 1234
of 3 October appointed X" is evidence.

The connector was written without network access to kenyalaw.org, so these
tests pin the two things that do not depend on the site's markup: that a
failure is never reported as an absence, and that the wiring is real.
"""

from datetime import datetime

import pytest

from engine.ingestion import http, queries
from engine.ingestion.kenya_law_connector import (KenyaGazetteConnector,
                                                  KenyaLawConnector)

WINDOW_START = datetime(2026, 9, 15)
WINDOW_END = datetime(2026, 10, 15)

PAGE = """
<html><body><ul>
  <li><a href="/judgment/kehc/2026/1234/">Republic v Cabinet Secretary;
      EPRA appointments, 3 October 2026</a></li>
  <li><a href="/akn/ke/judgment/kesc/2019/11/">Petition 12 of 2019,
      14 June 2019</a></li>
  <li><a href="/about/">About Kenya Law</a></li>
  <li><a href="/search/?page=2">Next</a></li>
</ul></body></html>
"""


class _Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fetch(monkeypatch, connector, page=PAGE, boom=None):
    def fake_get(url, **kw):
        if boom:
            raise boom
        return _Resp(page)
    monkeypatch.setattr(http, "get", fake_get)
    return connector.fetch("William Ruto", [], WINDOW_START, WINDOW_END)


def test_documents_are_found_and_navigation_is_not(monkeypatch):
    found = _fetch(monkeypatch, KenyaLawConnector())
    assert len(found) == 2, f"expected the two judgments, got {found}"
    assert all("/about/" not in m["raw_payload"]["url"] for m in found)
    assert all(m["raw_payload"]["url"].startswith("https://") for m in found), (
        "a relative href was stored, so the page cannot link it")


def test_a_refused_host_is_not_reported_as_an_empty_record(monkeypatch):
    """The founding rule, in the place it would do the most damage. "This
    person has no court record" is a publishable claim about a subject. A
    connection error must never produce it."""
    c = KenyaLawConnector()
    assert _fetch(monkeypatch, c, boom=RuntimeError("connection refused")) == []
    assert c.last_error, "a refused host returned [] and said nothing"
    assert "connection refused" in c.last_error


def test_a_page_that_parsed_to_nothing_says_which_nothing(monkeypatch):
    """A 200 with no document links is either a subject with no record or a
    parser that has stopped matching the site. Those are opposite findings
    and the caller cannot tell them apart from an empty list."""
    c = KenyaGazetteConnector()
    assert _fetch(monkeypatch, c, page="<html><body>no results</body></html>") == []
    assert c.last_error
    assert "markup changed" in c.last_error, (
        "the message does not raise the possibility that the parser is at "
        "fault, so a broken connector reads as a clean subject")


def test_a_successful_fetch_clears_the_previous_error(monkeypatch):
    """A connector reused across tasks must not carry a stale failure into a
    run that worked — the ledger would report a source as broken on the
    strength of a request that succeeded."""
    c = KenyaLawConnector()
    _fetch(monkeypatch, c, boom=RuntimeError("down"))
    assert c.last_error
    assert _fetch(monkeypatch, c)
    assert c.last_error is None


def test_an_old_judgment_keeps_its_own_date(monkeypatch):
    """A 2019 petition is a real finding about a subject and is not news
    from this month. Clamping it into the report window would date it to
    this week, which is a false fact in a document about a person."""
    found = _fetch(monkeypatch, KenyaLawConnector())
    dates = sorted(m["posted_at"] for m in found)
    assert dates[0].year == 2019, (
        f"the 2019 judgment was re-dated into the window: {dates}")


def test_these_documents_are_marked_as_primary_record(monkeypatch):
    """A gazette notice and a tweet are not the same kind of evidence and
    must not be weighed alike by anything downstream."""
    [first, *_] = _fetch(monkeypatch, KenyaGazetteConnector())
    assert first["raw_payload"]["record"] == "primary"
    assert first["source_type"] == "gazette"
    assert KenyaLawConnector().kind == "judgment"


def test_a_source_with_no_journalism_is_not_asked_journalism_questions():
    """DISCOVERY_PROBES asks what the press said. Kenya Law has no press, so
    those words appear in no judgment — and the learning loop would then
    retire a source that was asked the wrong question rather than being
    wrong."""
    from engine.db.models import Politician

    p = Politician(name="William Ruto", aliases=["Ruto"])
    gazette = " ".join(queries.connector_variants(p, "kenya_gazette"))
    law = " ".join(queries.connector_variants(p, "kenya_law"))
    assert "appointment" in gazette and "revocation" in gazette
    assert "petition" in law and "judicial review" in law
    for news_word in ("scandal", "criticised", "reaction", "biography"):
        assert news_word not in law, (
            f"{news_word!r} is a press question being asked of a court record")


def test_both_are_wired_into_a_run():
    """A connector nothing builds is a file, not a source."""
    from engine.ingestion import orchestrator

    assert "kenya_law" in orchestrator.KEYWORD_CONNECTORS
    assert "kenya_gazette" in orchestrator.KEYWORD_CONNECTORS

    class _Task:
        query = "William Ruto"
    for name, expected in (("kenya_law", KenyaLawConnector),
                           ("kenya_gazette", KenyaGazetteConnector)):
        _Task.connector = name
        assert isinstance(orchestrator._build_connector(_Task()), expected)

    body = (orchestrator.__file__ and
            open(orchestrator.__file__, encoding="utf-8").read())
    assert "settings.enable_kenya_gazette" in body, "never planned into a run"
    assert "settings.enable_kenya_law" in body


def test_kenya_law_is_paced():
    """It publishes the primary record for free and has no commercial
    interest in serving us. Being a good guest is the price of admission."""
    assert http.HOST_MIN_INTERVAL.get("new.kenyalaw.org", 0) >= 2.0


def test_the_long_budget_covers_them():
    """Truncating a gazette notice to a tweet's budget throws away the part
    a client is buying."""
    from engine.reports.analysts import _mention_budget

    assert _mention_budget("gazette") == _mention_budget("article")
    assert _mention_budget("judgment") == _mention_budget("article")


def test_no_test_hand_lists_the_source_flags():
    """Adding these two connectors broke four end-to-end tests in files that
    never mention Kenya.

    Six test files each kept their own copy of "every source flag", so two
    new sources stayed ON in tests that believed they had disabled
    everything. The symptom was not "a new source ran" — it was `ingestion
    did not run`, because a non-empty task list suppresses the mock-fixture
    fallback those tests depend on. The failure pointed three files away
    from the change that caused it.

    A hand-maintained list of everything is a promise to update six files
    whenever one file gains a feature. `conftest.source_flags()` discovers
    them instead, and this keeps it that way.
    """
    import re
    from pathlib import Path

    tests = Path(__file__).resolve().parent
    pattern = re.compile(r'for flag in \(\s*"enable_')
    offenders = [p.name for p in tests.glob("test_*.py")
                 if pattern.search(p.read_text())]
    assert not offenders, (
        "these files hand-list the source flags and will silently stop "
        f"disabling the next connector added: {offenders}. Use "
        "conftest.source_flags().")
