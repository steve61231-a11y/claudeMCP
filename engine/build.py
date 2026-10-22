"""Which code is running — one reader for every place that has to say so.

`/api/health` answers "is the fix deployed?", and every report records the
commit that wrote it, so a change can be judged by comparing the quality of
reports before and after it (`reports/quality.py`). Two readers of the same
fact must read it the same way, so both read it here.
"""

from __future__ import annotations

import os

#: Per host, and some only at build time — so they are the first choice and
#: not the only one.
_ENV = ("RAILWAY_GIT_COMMIT_SHA", "RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_COMMIT")

#: Written during the image build (see the Dockerfile), true on every host.
STAMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "BUILD_STAMP")


def commit_from_environment() -> str:
    for key in _ENV:
        value = os.environ.get(key, "")
        if value:
            return value
    return ""


def read_stamp() -> list[str]:
    """The build stamp's lines — commit, then build time — or [] without one."""
    try:
        with open(STAMP, encoding="utf-8") as handle:
            return [line.strip() for line in handle.read().splitlines()]
    except OSError:
        return []


def running_commit() -> str | None:
    """The short commit of the running code, or None when nothing says."""
    commit = commit_from_environment() or (read_stamp() or [""])[0]
    return commit[:7] or None
