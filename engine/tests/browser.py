"""Where this machine's Chromium is — found, not assumed.

Seven browser tests each hard-coded one version's path
(`/opt/pw-browsers/chromium-1194/...`). A fresh container ships a different
build, the path did not exist, and every one of those tests was SKIPPED —
which a test run reports as a pass. The page was then shipped on the strength
of tests that never ran. So the path is looked up: the newest Chromium under
the pre-installed browsers directory, wherever its version put it.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path("/opt/pw-browsers")
_LAYOUTS = ("chrome-linux64/chrome", "chrome-linux/chrome")


def find() -> Path:
    for build in sorted(ROOT.glob("chromium-*"), reverse=True):
        for layout in _LAYOUTS:
            candidate = build / layout
            if candidate.exists():
                return candidate
    return ROOT / "chromium" / "chrome-linux" / "chrome"  # absent: tests skip, and say why


CHROME = find()
