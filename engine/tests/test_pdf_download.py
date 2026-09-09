"""Send the report to someone outside the app — a file, not a link.

The client wants to hand a finished report to an outside analyst without
giving them a URL into a live, API-key-gated system. These endpoints render
the exact stored payload through the app's own rendering code
(window.ZENITH.renderReport / renderIssue) and return it as a downloadable
PDF — so a PDF can never show something the live page wouldn't.
"""

import re
import pytest
from fastapi.testclient import TestClient

from engine.reports import pdf_export

pytestmark = pytest.mark.skipif(not pdf_export.chromium_available(),
                                reason="Chromium not available in this environment")


@pytest.fixture()
def client(monkeypatch):
    from engine import api_server

    monkeypatch.setattr(api_server, "_require_api_key", lambda x: None)
    return TestClient(api_server.app)


def _seed_progress(monkeypatch, kind: str, subject_key: str, payload: dict):
    from engine import api_server

    stored = {kind: {"payload": payload}}
    monkeypatch.setattr(
        api_server, "_read_progress",
        lambda key, k: stored.get(k) if key == subject_key else None)


def test_downloading_a_report_returns_a_real_pdf(client, monkeypatch):
    from engine import api_server

    payload = {"name": "Test Subject", "window": "2026-06-01 – 2026-06-22",
              "sentiment": {"positive": 40, "neutral": 35, "negative": 25},
              "volume": {"total": 10, "platforms": 2}, "narratives": [],
              "influence": [], "coverage": {}, "grade": {"production": False}}
    _seed_progress(monkeypatch, "report", api_server._subject_key("Test Subject"), payload)

    res = client.get("/api/report/download?name=Test%20Subject")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content[:5] == b"%PDF-"
    assert "attachment" in res.headers["content-disposition"]
    assert "test-subject" in res.headers["content-disposition"]


def test_downloading_an_issue_map_returns_a_real_pdf(client, monkeypatch):
    from engine import api_server

    payload = {"principal": "Okiya Omtatah", "issue": "IMF",
              "intersection": {"verdict": "Contested.", "key_actors": [],
                               "linking_narratives": [], "timeline": [], "sub_issues": []},
              "issue_graph": {"nodes": [], "edges": [], "legend": [],
                              "stats": {"nodes": 0, "edges": 0}},
              "coverage": {}, "evidence_sample": [], "thin": False, "issue_framework": None}
    key = api_server._subject_key("Okiya Omtatah", "IMF")
    _seed_progress(monkeypatch, "issue_map", key, payload)

    res = client.get("/api/issue-map/download?principal=Okiya%20Omtatah&issue=IMF")
    assert res.status_code == 200
    assert res.content[:5] == b"%PDF-"
    assert "okiya-omtatah-x-imf" in res.headers["content-disposition"]


def test_downloading_with_nothing_stored_is_a_clean_404(client, monkeypatch):
    from engine import api_server

    monkeypatch.setattr(api_server, "_read_progress", lambda key, kind: None)
    res = client.get("/api/report/download?name=Nobody")
    assert res.status_code == 404


def test_the_pdf_is_not_trivially_empty(client, monkeypatch):
    """A blank PDF would still pass the magic-bytes check; also require a
    minimum size, since a render that silently failed tends to produce a
    tiny near-empty document."""
    from engine import api_server

    payload = {"name": "Test Subject", "sentiment": {}, "volume": {"total": 5},
              "narratives": [], "influence": [], "coverage": {}}
    _seed_progress(monkeypatch, "report", api_server._subject_key("Test Subject"), payload)

    res = client.get("/api/report/download?name=Test%20Subject")
    assert len(res.content) > 5000


# --- the button must not need a browser on the SERVER -------------------------

def test_the_button_saves_a_pdf_file_with_no_dialog():
    """Three wrong answers preceded this one: a 503 from a hard-coded browser
    path, a printer prompt, and an .html file that was not the PDF asked for.
    It must fetch the real PDF and save it."""
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    # The CALL, not the word: the comment explaining why printing was wrong
    # must not itself fail this test.
    assert not re.search(r"^\s*window\.print\(\)", page, re.M), \
        "still opening a print dialog"
    assert "/api/report/download" in page, "the button does not fetch a PDF"
    assert "a.download" in page and "URL.createObjectURL" in page, \
        "the button does not save a file"
    assert "'.pdf'" in page or '".pdf"' in page, "not saving with a .pdf name"


def test_a_missing_browser_still_hands_the_reader_a_file():
    """A file in hand beats an error message."""
    from engine.api_server import render_frontend_document

    assert "saveSelfContainedHtml" in render_frontend_document()


def test_the_printed_page_is_recoloured_for_paper():
    """The UI is dark. Printed unchanged it is an unreadable slab of ink."""
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    assert "@media print" in page
    assert "@page" in page


def test_a_citation_carries_its_address_onto_the_paper():
    """A link is worthless in a printed file unless the URL is printed too —
    and the whole point of this export is that it leaves the app."""
    from engine.api_server import render_frontend_document

    assert 'a.src-link[href^="http"]::after' in render_frontend_document()


def test_chromium_is_found_rather_than_assumed(tmp_path, monkeypatch):
    """The path was hard-coded to one container's Playwright install, so this
    reported "not available" everywhere else. It is now discovered, and an
    operator can name it outright."""
    from engine.reports import pdf_export

    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv(pdf_export._ENV_OVERRIDE, str(fake))
    assert pdf_export.chrome_path() == fake
    assert pdf_export.chromium_available()


def test_no_browser_anywhere_is_reported_honestly(monkeypatch):
    from engine.reports import pdf_export

    monkeypatch.delenv(pdf_export._ENV_OVERRIDE, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(pdf_export, "_CANDIDATE_PATHS", ())
    monkeypatch.setattr(pdf_export.shutil, "which", lambda _name: None)
    assert pdf_export.chrome_path() is None
    assert not pdf_export.chromium_available()


# --- the boot block must land in the document, not inside a string -----------

def test_the_boot_block_goes_after_the_LAST_closing_body_tag():
    """A shipped PDF was eighteen pages of this app's own JavaScript.

    The boot block was spliced with `.replace("</body>", ..., 1)`, and the
    FIRST </body> in this document is not the closing tag — it is inside a
    JavaScript string, in the client-side "download as HTML" helper that
    builds a document by concatenation:

        +view.innerHTML+'</main></div></body></html>';

    So the block landed inside a string literal, mid-script, and its own
    </script> closed the page's script tag early. Every remaining line of
    source then rendered as visible body text.
    """
    from engine.api_server import render_frontend_document
    from engine.reports import pdf_export

    doc = render_frontend_document()
    assert doc.find("</body>") != doc.rfind("</body>"), (
        "this document no longer has a decoy </body>; the test needs a new one")

    captured = {}

    def _fake_run(cmd, **kwargs):
        for arg in cmd:
            if isinstance(arg, str) and arg.startswith("--print-to-pdf="):
                target = pathlib.Path(arg.split("=", 1)[1])
                target.write_bytes(b"%PDF-1.4 stub")
        src = [a for a in cmd if isinstance(a, str) and a.startswith("file://")][0]
        captured["html"] = pathlib.Path(src[7:]).read_text(encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    import pathlib
    import types

    import engine.reports.pdf_export as _px
    original = _px.subprocess.run if hasattr(_px, "subprocess") else None
    monkey = pytest.MonkeyPatch()
    monkey.setattr(_px, "chrome_path", lambda: pathlib.Path("/bin/true"))
    monkey.setattr("subprocess.run", _fake_run)
    try:
        pdf_export.render_payload_to_pdf(doc, "report", {"name": "X"})
    finally:
        monkey.undo()

    html = captured["html"]
    boot_at = html.find('id="pdf-payload"')
    assert boot_at > 0, "the boot block never made it into the document"
    # It must sit after the page's own script has closed, not inside it.
    assert boot_at > html.rfind("})();"), \
        "the boot block landed inside the page's script"


def test_the_rendered_pdf_is_the_report_not_the_source_code():
    """The shipped defect in one assertion."""
    import inspect

    from engine.reports import pdf_export

    source = inspect.getsource(pdf_export.render_payload_to_pdf)
    assert 'rfind("</body>")' in source
    # Code only — the comment above the fix quotes the old call deliberately,
    # and must not fail the test that guards it.
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    assert '.replace("</body>"' not in code, \
        "back to splicing at the first </body>, which is inside a JS string"
