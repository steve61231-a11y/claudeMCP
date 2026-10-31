"""How much memory this process holds, against how much it is allowed.

Railway's only word on the matter was an email — "your deployment ran out of
memory and crashed" — after the fact, with no stage, no figure and no limit.
A crash of that kind kills every run in flight and leaves nothing in the
ledger, so each one looked like a report that simply never finished. The
founding rule at the scale of the process: a run killed for memory must not
look like a run that is still thinking.

So the engine reads its own memory (Linux `/proc/self/status`: VmRSS now,
VmHWM the high-water mark) and the container's limit (cgroup v2
`memory.max`, or v1 `memory.limit_in_bytes`), reports both at `/api/health`,
records the figure at every stage lap so a report says where memory went as
well as where time went, and refuses NEW work near the limit — a clear
"try again in a few minutes" beats a crash that takes the runs already in
flight down with it. Every reading degrades to None, never to zero: an
unreadable limit is not a limit of nothing.
"""

from __future__ import annotations

from pathlib import Path

#: Refuse to START new work above this share of the limit. Work already
#: running is never stopped by this — that would be a crash by other means.
ADMIT_BELOW = 0.85

_CGROUP_FILES = ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
#: cgroup v1 reports "no limit" as a number near 2**63; anything above this
#: is the host's, not a container limit.
_NO_LIMIT = 1 << 60


def _status_kb(field: str) -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def rss_mb() -> float | None:
    kb = _status_kb("VmRSS")
    return round(kb / 1024, 1) if kb is not None else None


def peak_mb() -> float | None:
    kb = _status_kb("VmHWM")
    return round(kb / 1024, 1) if kb is not None else None


def limit_mb(files: tuple[str, ...] = _CGROUP_FILES) -> float | None:
    for name in files:
        try:
            raw = Path(name).read_text().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        if 0 < value < _NO_LIMIT:
            return round(value / (1024 * 1024), 1)
        return None
    return None


def snapshot() -> dict:
    rss, peak, limit = rss_mb(), peak_mb(), limit_mb()
    out = {"rss_mb": rss, "peak_mb": peak, "limit_mb": limit}
    if rss is not None and limit:
        out["share"] = round(rss / limit, 2)
    if limit is None:
        out["limit_note"] = "no container memory limit could be read"
    return out


def refusal() -> str | None:
    """Why new work must wait, or None. Only ever a reason when BOTH
    figures were read: an unknown is never a refusal."""
    rss, limit = rss_mb(), limit_mb()
    if rss is None or not limit or rss < ADMIT_BELOW * limit:
        return None
    return (f"The engine is using {int(rss)} MB of its {int(limit)} MB memory limit, so a new run "
            f"was not started — starting one now could crash the runs already in progress. "
            f"Try again in a few minutes, when they have finished.")
