"""Can THIS server reach the primary-record hosts — measured where it runs.

Phase 1 of `docs/ISSUE_MAP_FOUNDATION.md` reads records a connection is
made on: Hansard (Mzalendo), Kenya Law's judgments and cause lists, the
Gazette, Parliament's order papers, the World Bank. GitHub's network
reaches them (`scripts/source_probe.py`); Railway's address was refused by
Kenya Law before, and what one datacenter is allowed says nothing about
another. A connector built on a host the server cannot reach would report
"no court record" for a person with one — the founding rule broken by a
firewall. So the server measures its own reach and `/api/health` says it.

The hosts and paths are public and name no subject: whom anyone is
researching never leaves this module. Measured in a background thread and
cached for six hours, because a health check answers in milliseconds and
must never wait on six foreign servers.
"""

from __future__ import annotations

import threading
import time

#: label -> a public URL on the host, asked about a public topic.
HOSTS = {
    "mzalendo_hansard": "https://info.mzalendo.com/api/v0.1/hansard/sittings/?format=json",
    "kenya_law_causelists": "https://new.kenyalaw.org/causelists/",
    "kenya_law_gazettes": "https://new.kenyalaw.org/gazettes/",
    "parliament_order_papers": "https://www.parliament.go.ke/the-national-assembly/house-business/order-paper",
    "world_bank": "https://search.worldbank.org/api/v2/projects?format=json&countrycode_exact=KE&rows=1",
    "kenya_law_search": "https://new.kenyalaw.org/search/api/documents/?search=health%20insurance",
    # A query whose answer is longer than the 50-byte floor: ASK{} answers
    # in 30 bytes, so a working Wikidata read as unreachable.
    "wikidata": "https://query.wikidata.org/sparql?format=json&query=SELECT%20%3Fh%20WHERE%20%7Bwd%3AQ114%20wdt%3AP35%20%3Fh%7D",
}

TTL_SECONDS = 6 * 3600
#: Off in the test suite (conftest), which cannot reach any of these hosts.
ENABLED = True
_lock = threading.Lock()
_state: dict = {"measured_at": None, "hosts": {}, "running": False}


def _check(url: str) -> dict:
    from engine.ingestion import http

    started = time.monotonic()
    try:
        response = http.get(url, timeout=20)
        ok = 200 <= response.status_code < 300 and len(response.content or b"") > 50
        return {"ok": ok, "status": response.status_code, "bytes": len(response.content or b""),
                "seconds": round(time.monotonic() - started, 1)}
    except Exception as exc:  # noqa: BLE001 — an unreachable host is the finding
        return {"ok": False, "status": None, "error": f"{type(exc).__name__}: {exc}"[:160],
                "seconds": round(time.monotonic() - started, 1)}


def _measure() -> None:
    results = {label: _check(url) for label, url in HOSTS.items()}
    with _lock:
        _state.update({"measured_at": time.time(), "hosts": results, "running": False})


def reach() -> dict:
    """The last measurement, starting a new one in the background when it
    is missing or older than six hours. Never blocks, never raises."""
    if not ENABLED:
        return {"state": "not_measured", "reason": "switched off (test environment)"}
    with _lock:
        stale = _state["measured_at"] is None or time.time() - _state["measured_at"] > TTL_SECONDS
        if stale and not _state["running"]:
            _state["running"] = True
            threading.Thread(target=_measure, daemon=True).start()
        if _state["measured_at"] is None:
            return {"state": "measuring", "note": "the first measurement is running; ask again in a minute"}
        return {"state": "measured",
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_state["measured_at"])),
                "hosts": dict(_state["hosts"])}
