"""Issue-map timelines must carry real dates.

Every GDELT article at the intersection used to be stamped with the window end,
so dozens of articles "happened" on the same day and the analyst dated its
timeline from that. The timeline looked authoritative and every date in it was
invented. GDELT ships the real timestamp in `seendate`.
"""

from datetime import datetime, timedelta

from engine.reports import issue_map


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_gdelt_intersection_uses_the_article_timestamp(monkeypatch):
    we = datetime(2026, 7, 1)
    ws = we - timedelta(days=365)
    articles = [
        {"url": "https://a.example/1", "title": "One", "seendate": "20260210T090000Z", "domain": "a.example"},
        {"url": "https://a.example/2", "title": "Two", "seendate": "20260615T113000Z", "domain": "a.example"},
        # No seendate at all: falls back to the window end rather than crashing.
        {"url": "https://a.example/3", "title": "Three", "domain": "a.example"},
        # Older than the window: clamped in, never left outside it.
        {"url": "https://a.example/4", "title": "Four", "seendate": "20200101T000000Z", "domain": "a.example"},
    ]
    monkeypatch.setattr(issue_map.http, "get", lambda *a, **k: _Resp({"articles": articles}))

    out = issue_map._gdelt_intersection("Ruto", "SHA", ws, we)
    dates = [m["posted_at"] for m in out]

    assert dates[0] == datetime(2026, 2, 10, 9, 0, 0)
    assert dates[1] == datetime(2026, 6, 15, 11, 30, 0)
    assert dates[2] == we
    assert dates[3] == ws  # clamped, not dropped and not fabricated
    # The bug: every article sharing one date.
    assert len(set(dates)) > 1
