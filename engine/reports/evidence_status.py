"""How much each section of the report can bear — said on the section itself.

Phase 3's last promise in docs/FOUNDATION.md: "the fixed report: same
sections every run, each stating whether its evidence is complete, thin or
failed." A section written from the verified record and a section an
analyst wrote with nothing checking it rendered identically — a client had
no way to know which sentences to repeat in a meeting and which to treat as
one analyst's reading. That is the founding rule pointed at trust rather
than at failure: a section must never look stronger than its evidence.

Each section gets one grade, computed from what the payload says about how
it was made — never from a model's view of itself:

- `record`     written from, or listing, the verified record
- `mixed`      partly on the record, partly the analyst's
- `counted`    arithmetic over what was collected; no model wrote it
- `quoted`     an analyst's summary beside quotes each found in its source
- `analyst`    an analyst's reading, not checked against the record
- `thin`       on the record, but resting on very little
- `unavailable` not produced this run — the operator's to see; the reader
               is never shown an empty section
"""

from __future__ import annotations

LABELS = {
    "record": "On the record",
    "mixed": "Partly on the record",
    "counted": "Counted",
    "quoted": "Quotes checked",
    "analyst": "Analyst's reading",
    "thin": "Thin evidence",
    "unavailable": "Not available this run",
}


def _grade(grade: str, detail: str) -> dict:
    return {"grade": grade, "label": LABELS[grade], "detail": detail}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def established_record(payload: dict) -> dict:
    rec = payload.get("established_record") or {}
    if not rec:
        return _grade("unavailable", "the record could not be read this run")
    total = int(rec.get("findings_total") or len(rec.get("findings") or []))
    corroborated = sum(1 for f in rec.get("findings") or []
                       if int(f.get("independent_sources") or 1) >= 2)
    if not total:
        return _grade("thin", "no finding on the record names the subject this period")
    if total < 3:
        return _grade("thin", f"only {_plural(total, 'finding')} on the record")
    return _grade("record", f"{_plural(total, 'finding')}, {corroborated} independently corroborated")


def executive_brief(payload: dict) -> dict:
    if payload.get("brief_basis") == "verified_facts":
        removed = len(((payload.get("brief_check") or {}).get("check") or {}).get("removed") or [])
        return _grade("record", "written only from the findings on the record; every sentence cites them"
                      + (f"; {_plural(removed, 'sentence')} the check could not support removed" if removed else ""))
    return _grade("analyst", "the analysts' synthesis — not checked sentence by sentence against the record")


def risks(payload: dict) -> dict:
    if payload.get("watch_basis") == "verified_facts":
        return _grade("record", "each item a finding on the record; the assessment beside it is labelled as judgement")
    return _grade("analyst", "the analysts' reading of the coverage")


def deep_insights(payload: dict) -> dict:
    rows = (payload.get("deep_insights") or {}).get("insights") or []
    on = [i for i in rows if isinstance(i, dict) and i.get("basis") == "verified_facts"]
    if not rows:
        return _grade("unavailable", "no reading was produced this run")
    if on:
        return _grade("record", "each insight rests on the findings it cites; its strength is counted from them")
    return _grade("analyst", "the analysts' reading of a digest of the coverage")


def search_demand(payload: dict) -> dict:
    sd = payload.get("search_demand") or {}
    state = sd.get("state")
    if state == "measured":
        return _grade("counted", "Google searches and suggestions and Wikipedia readers, counted; no model wrote it")
    if state == "too_little_search":
        return _grade("thin", "too few searches for Google to publish a figure")
    return _grade("unavailable", sd.get("reason") or "search demand was not measured this run")


def timeline(payload: dict) -> dict:
    rows = [t for t in payload.get("timeline") or [] if isinstance(t, dict) and not t.get("before_period")]
    if not rows:
        return _grade("unavailable", "no dated moment was produced this run")
    on = sum(1 for t in rows if t.get("basis") == "verified_facts")
    if on == len(rows):
        return _grade("record", f"{_plural(on, 'day')}, each dated by first publication and written from the record")
    if on:
        return _grade("mixed", f"{on} of {len(rows)} days on the record; the rest are analyst briefings")
    return _grade("quoted", "analyst briefings; the quotes under them are checked against their sources")


def narrative_deep_dives(payload: dict) -> dict:
    dives = [d for d in payload.get("narrative_deep_dives") or [] if isinstance(d, dict)]
    if not dives:
        return _grade("unavailable", "no storyline was traced this run")
    traced = sum(1 for d in dives if d.get("unfolded_basis") == "verified_facts")
    if traced == len(dives):
        return _grade("record", f"every storyline traced from its own findings on the record")
    if traced:
        return _grade("mixed", f"{traced} of {len(dives)} storylines traced on the record; the rest are the analysts'")
    return _grade("quoted", "the analysts' accounts; the quotes in them are checked against their sources")


def _quoted_section(rows: list, what: str) -> dict:
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return _grade("unavailable", f"no {what} was produced this run")
    quotes = sum(len(r.get("quotes") or []) for r in rows)
    unquoted = sum(1 for r in rows if not r.get("quotes"))
    if quotes and not unquoted:
        return _grade("quoted", f"the analysts' reading; {_plural(quotes, 'quote')} each found in its source")
    if quotes:
        return _grade("quoted", f"the analysts' reading; {_plural(quotes, 'quote')} found in their sources, "
                                f"{unquoted} of {len(rows)} without one")
    return _grade("analyst", "the analysts' reading; no quote under it could be found in a source")


def public_voice(payload: dict) -> dict:
    voice = payload.get("public_voice") or {}
    themes = [t for stance in ("supportive", "critical", "neutral") for t in voice.get(stance) or []]
    return _quoted_section(themes, "public voice")


def platform_pulse(payload: dict) -> dict:
    return _quoted_section(payload.get("platform_pulse") or [], "platform reading")


def influencer_stances(payload: dict) -> dict:
    return _quoted_section(payload.get("influencer_stances") or [], "reading of the accounts")


def narratives(payload: dict) -> dict:
    rows = payload.get("narrative_breakdown") or []
    if not rows:
        return _grade("unavailable", "no storyline was found this run")
    return _grade("counted", "grouped by the engine from the items themselves and counted; the labels are a model's")


SECTIONS = {
    "established_record": established_record,
    "executive_brief": executive_brief,
    "risks": risks,
    "deep_insights": deep_insights,
    "search_demand": search_demand,
    "narrative_breakdown": narratives,
    "public_voice": public_voice,
    "timeline": timeline,
    "platform_pulse": platform_pulse,
    "influencer_stances": influencer_stances,
    "narrative_deep_dives": narrative_deep_dives,
}


def assess(payload: dict) -> dict:
    """{section: {"grade", "label", "detail"}} — one line per section."""
    out = {}
    for key, fn in SECTIONS.items():
        try:
            out[key] = fn(payload if payload is not None else {})
        except Exception as exc:  # noqa: BLE001 — a status we cannot compute is said, not guessed
            out[key] = {"grade": "unavailable", "label": "Not assessed",
                        "detail": f"the evidence status could not be computed ({type(exc).__name__})"}
    return out
