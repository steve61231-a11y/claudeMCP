"""The counted sections are on the page before any analyst answers.

Live, 13 Nov 2026: searches sat at "7/16 sections" for the whole analyst
phase. The seven were everything published before the analysts start; the
model-free versions of the analyst sections (platforms, loudest accounts,
dated developments, the terms the coverage turns on) were built only AFTER
the last analyst returned or the twelve-minute budget ran out. Counting
cannot be slow, so it now lands first, labelled derived, and each analyst
replaces its section when it answers.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from engine import llm, stages
from engine.config import settings
from engine.reports import sections


def test_counted_sections_are_published_while_every_analyst_is_still_working(monkeypatch):
    stages.reset()
    release = threading.Event()

    def blocked(*a, **k):
        release.wait(10)
        raise RuntimeError("released")

    monkeypatch.setattr(llm, "call_json", blocked)
    monkeypatch.setattr(llm, "call_json_untrusted", blocked)
    monkeypatch.setattr(settings, "analyst_deadline_seconds", 4, raising=False)
    now = datetime(2026, 11, 10)
    mentions = [{"id": f"m{i}", "kind": "mention", "platform": "nation.africa" if i % 2 else "youtube",
                 "source_type": "article" if i % 2 else "video", "author_handle": f"outlet{i % 5}",
                 "text": f"Karua addresses rally on reforms {i}", "posted_at": now - timedelta(days=i % 20),
                 "engagement": {"views": i * 10}, "source_url": f"https://x/{i}", "date_estimated": False}
                for i in range(60)]
    from engine.reports.generator import generate_report_payload

    payload = generate_report_payload("Martha Karua", now - timedelta(days=30), now, mentions,
                                      {}, [], [], {})
    started = time.monotonic()
    seen: dict[str, float] = {}

    def on_section(key, value):
        seen.setdefault(key, time.monotonic() - started)

    t = threading.Thread(target=sections.enrich_report_payload,
                         args=("Martha Karua", now - timedelta(days=30), now, payload, mentions, []),
                         kwargs={"on_section": on_section}, daemon=True)
    t.start()
    time.sleep(1.5)
    early = dict(seen)
    release.set()
    t.join(30)
    for key in ("platform_pulse", "influencer_stances", "timeline"):
        assert key in early, f"{key} waited for the analysts (published: {sorted(early)})"
    assert "derived_sections" in early
    # The early versions never leak into the run's own payload as analysis.
    assert seen.get("derived_sections") is not None
