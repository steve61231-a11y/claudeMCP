"""Also the timeline shit ain't working.

An investor, about a live report, and the screenshots agreed. The key-dates
strip was a row of identical dots whose date and event existed only in a
hover — so on a phone, on paper and at a glance it said nothing. Dates the
analysts wrote out ("15 October 2026") could not be parsed, fell off the
axis, and enough of them put the strip into "Not enough distinct dates to
scale by time — spaced evenly". And a one-month report's timeline could open
on the last election, because a mention published this month that says
"since he was elected in 2022" was dated by the moment it describes.
"""

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from engine.reports import analysts
from engine.reports import sentiment_framework as fw

CHROME = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")


def test_a_moment_before_the_period_is_marked_as_context(monkeypatch):
    mentions = [{"id": i, "text": "t", "platform": "news", "author_handle": "a",
                 "posted_at": datetime(2026, 10, d), "engagement": {}}
                for i, d in enumerate((2, 9, 16), start=1)]
    monkeypatch.setattr(analysts, "_timeline_slice", lambda name, half, refs: [
        {"date": "2022-08-09", "event": "Elected deputy president"},
        {"date": "2026-10-09", "event": "Impeachment appeal admitted"},
        {"date": "mid-2025", "event": "Loose phrasing is not placed"},
    ])
    timeline = analysts.analyze_timeline("X", mentions, {"2026-10-09": 1})
    by_event = {t["event"]: t for t in timeline}
    assert by_event["Elected deputy president"].get("before_period") is True
    assert not by_event["Impeachment appeal admitted"].get("before_period")
    assert not by_event["Loose phrasing is not placed"].get("before_period")


def test_the_issue_s_dates_leave_out_context_from_before_the_period():
    payload = {"timeline": [
        {"date": "2022-08-09", "event": "Impeachment appeal history", "before_period": True},
        {"date": "2026-10-09", "event": "Impeachment appeal admitted by the High Court"},
    ]}
    prof = {"_items": [], "by_day": {}, "peak": None}
    dates = fw._issue_dates(prof, {"label": "Impeachment appeal",
                                   "description": "High Court appeal"}, payload, None)
    assert [d["date"] for d in dates] == ["2026-10-09"]


def test_an_all_editorial_window_is_shared_by_items_not_by_zero_engagement():
    items = [{"mention_id": str(i), "headline": h, "platform": "news", "engagement": 0,
              "outlet_type": "local_media"}
             for i, h in enumerate(["Budget vote delayed in Senate",
                                    "Budget vote delayed again in Senate",
                                    "Teachers strike enters day three"])]
    out = fw.group_emergent(items)
    assert out["share_basis"] == "items"
    assert sorted(t["share"] for t in out["themes"]) == [33.3, 66.7]


# --- in the browser ---------------------------------------------------------

def _server(html: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body, ctype = html.encode(), "text/html; charset=utf-8"
            if self.path.startswith("/api/") or self.path.startswith("/health"):
                body, ctype = b'{"ok": false}', "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


FRAMEWORK = {
    "strategic_implications": {
        "issue": "Impeachment appeal", "outline": "o", "status": "s",
        "key_dates": [
            {"date": "15 October 2026", "event": "Appeal admitted for hearing", "kind": "briefing"},
            {"date": "2nd October 2026", "event": "Petition filed at the High Court", "kind": "briefing"},
            {"date": "October 19, 2026", "event": "Ruling reserved", "kind": "briefing"},
        ],
    },
}

READ = """(fw) => {
  const out = document.createElement('div');
  document.body.appendChild(out);
  window.ZENITH.renderSentimentFramework(out, fw, 'X');
  const strip = out.querySelector('.tl-strip');
  return {
    numbers: [...strip.querySelectorAll('.tl-num')].map(n => n.textContent),
    hint: strip.innerText,
    listed: [...out.querySelectorAll('.mentr .tl-key-n')].map(n => n.textContent),
    text: out.innerText,
  };
}"""


@pytest.mark.skipif(not CHROME.exists(), reason="Chromium not available in this environment")
def test_written_out_dates_are_placed_in_time_and_every_moment_is_numbered():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from engine.api_server import render_frontend_document

    server = _server(render_frontend_document())
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            page.wait_for_function("() => window.ZENITH && window.ZENITH.renderSentimentFramework")
            seen = page.evaluate(READ, json.loads(json.dumps(FRAMEWORK)))
            browser.close()
    finally:
        server.shutdown()
    assert not errors, errors
    assert "Spaced by real date" in seen["hint"], seen["hint"]
    assert sorted(seen["numbers"]) == ["1", "2", "3"]
    # The list under the strip carries the same numbers — in date order, so
    # the 2 October petition is 1 whatever order the analyst listed it in.
    assert seen["listed"] == ["2", "1", "3"]
    assert "Petition filed at the High Court" in seen["text"]
