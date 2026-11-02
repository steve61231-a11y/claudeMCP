"""How much each section of the Issue Map can bear — said on the section itself.

Phase 3 of docs/ISSUE_MAP_FOUNDATION.md: "evidence status stamped on every
framework section, as on the Search report". The map's sections rendered
alike whether the record wrote them, arithmetic counted them, or a model
wrote them with nothing checking — so a client could not tell which
sentences to repeat in a meeting. The founding rule pointed at trust: a
section must never look stronger than its evidence.

The grades and labels are the Search report's (`evidence_status.LABELS`),
so a reader learns one vocabulary; this module is the map's own, so the
Search report's grading is untouched. Each grade is computed from how the
payload says the section was made — never from a model's view of itself.
"""

from __future__ import annotations

from engine.reports.evidence_status import _grade, _plural


def connection_record(payload: dict) -> dict:
    cr = payload.get("connection_record") or {}
    if cr.get("state") != "measured":
        return _grade("unavailable", cr.get("reason") or "the record could not be read this run")
    n = int((cr.get("counts") or {}).get("findings") or 0)
    corroborated = int((cr.get("counts") or {}).get("corroborated") or 0)
    if not n:
        return _grade("thin", "no verified statement names both")
    if n < 3:
        return _grade("thin", f"only {_plural(n, 'verified statement')} name both")
    return _grade("record", f"{_plural(n, 'verified statement')}, {corroborated} independently corroborated")


def connection_paths(payload: dict) -> dict:
    cp = payload.get("connection_paths") or {}
    if cp.get("state") != "measured":
        return _grade("unavailable", cp.get("reason") or "the records were not read this run")
    total = int(cp.get("paths_total") or 0)
    read = sum(int(v or 0) for v in (cp.get("reach") or {}).values())
    if not total:
        return _grade("thin", f"{_plural(read, 'record')} on the issue read; none names them in a role")
    return _grade("record", f"{_plural(total, 'role')} stated by public records")


def verdict(payload: dict) -> dict:
    vr = payload.get("verdict_from_record") or {}
    if vr.get("used"):
        kept = int((vr.get("check") or {}).get("kept") or 0)
        return _grade("record", f"written from the record; {_plural(kept, 'sentence')}, each checked "
                                "against what it cites")
    return _grade("analyst", "our analysts' reading of the documents; the record's count stands beside it")


def scenarios(payload: dict) -> dict:
    sc = payload.get("scenarios") or {}
    if sc.get("state") != "modelled":
        return _grade("unavailable", sc.get("reason") or "not modelled this run")
    return _grade("mixed", "the probabilities are judgement; every factor cites a finding, and each "
                           "scenario's figure is recomputed from its branches")


def background(payload: dict) -> dict:
    return _grade("analyst", "the analysts' account of the issue's history, from the documents read")


def stakeholders(payload: dict) -> dict:
    rec = (((payload.get("issue_framework") or {}).get("main_contours") or {}).get("record") or {})
    if rec.get("state") == "measured" and rec.get("on_record"):
        return _grade("mixed", f"{rec['on_record']} of {_plural(int(rec.get('stakeholders') or 0), 'stakeholder')} "
                               "named with the issue in verified statements; who stands where is the "
                               "analysts' reading")
    return _grade("analyst", "who stands where is the analysts' reading of the coverage")


def recommendations(payload: dict) -> dict:
    if (payload.get("scenarios") or {}).get("state") == "modelled":
        return _grade("mixed", "the baseline is the scenario tree's arithmetic; recommendations name only "
                               "who the record names")
    return _grade("analyst", "the analysts' recommendations; no scenario tree was modelled to hold them to")


def sequencing(payload: dict) -> dict:
    timeline = (((payload.get("issue_framework") or {}).get("sequencing") or {})
                .get("engagement_timeline") or [])
    stated = sum(1 for x in timeline if x.get("source") == "stated")
    if not timeline:
        return _grade("thin", "no date ahead was found")
    if stated == len(timeline):
        return _grade("record", f"{_plural(stated, 'date')} ahead, each stated by a source")
    if stated:
        return _grade("mixed", f"{stated} of {len(timeline)} dates ahead stated by a source; the rest "
                               "are the analysts'")
    return _grade("analyst", "the dates are the analysts' reading; no source states a date ahead")


def data_overview(payload: dict) -> dict:
    return _grade("counted", "what was read, set aside and checked, counted; no model wrote it")


SECTIONS = {
    "connection_record": connection_record,
    "connection_paths": connection_paths,
    "verdict": verdict,
    "scenarios": scenarios,
    "background": background,
    "stakeholders": stakeholders,
    "recommendations": recommendations,
    "sequencing": sequencing,
    "data_overview": data_overview,
}


def assess(payload: dict) -> dict:
    """Every section's grade. One that cannot be graded says so rather than
    taking a grade it did not earn."""
    out = {}
    for key, fn in SECTIONS.items():
        try:
            out[key] = fn(payload or {})
        except Exception:  # noqa: BLE001
            out[key] = {"grade": None, "label": "Not assessed", "detail": "could not be graded"}
    return out
