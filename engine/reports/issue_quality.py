"""How good is this Issue Map — measured, not believed.

The Search report has carried `quality` on every run since Phase 0 of
`docs/FOUNDATION.md`; the map carried nothing, so no change to it could be
shown to be better, and a map that read sixteen documents, all of them
YouTube, looked no different from one that read the country's newsrooms.
This is Phase 0 of `docs/ISSUE_MAP_FOUNDATION.md`, computed from the map and
the corpus it was written from with no model involved:

  * **What was read** — items by kind and by segment, Kenyan outlets, how
    many articles were read in full, how many items are the state's own
    records, how much is reaction rather than publication, and what the
    relevance pools kept on each half of the question.
  * **What the page rests on** — client-facing sentences written from the
    verified record (the connection, the scenarios) against those an
    analyst wrote over a digest with nothing checking them (verdict,
    involvement, tension, sub-issues, storylines). The share is the number
    each later phase is judged by.
  * **What the guards removed**, so a cleaner map is not mistaken for a
    truer one, and what the paid sources cost.

It reports; it does not edit. Stored on the map as `quality`, stamped with
the commit that wrote it, listed with the Search report's at
`/api/admin/quality`.
"""

from __future__ import annotations

from datetime import datetime

from engine.build import running_commit
from engine.reports import outlets
from engine.reports.prose import sentences

VERSION = 1

#: The state's own records: judgments and gazette notices (as the Search
#: report counts them), Hansard speeches, bills and acts, other Kenya Law
#: documents (engine/ingestion/kenya_law_records.py, parliament_records.py).
PRIMARY_KINDS = ("judgment", "gazette", "hansard", "bill", "legislation", "record")

#: The analysts' prose — written over a digest, no sentence checked.
UNCHECKED = ("verdict", "involvement", "tension_or_risk")


def _count(text) -> int:
    return len([s for s in sentences(str(text or "")) if len(s.strip()) > 20])


def corpus_read(corpus: list[dict]) -> dict:
    items = len(corpus)
    kinds: dict[str, int] = {}
    segments: dict[str, int] = {}
    kenyan_outlets: set[str] = set()
    for m in corpus:
        kinds[m.get("source_type") or "unknown"] = kinds.get(m.get("source_type") or "unknown", 0) + 1
        try:
            segment, _ = outlets.classify(m)
        except Exception:  # noqa: BLE001 — a row the classifier cannot read is still counted
            segment = "unclassified"
        segments[segment] = segments.get(segment, 0) + 1
        if segment == outlets.LOCAL and m.get("source_type") != "comment":
            kenyan_outlets.add(outlets.publisher(m))
    articles = [m for m in corpus if m.get("source_type") == "article"]
    full = sum(1 for m in articles if m.get("full_text") or m.get("kind") == "document")
    primary = sum(1 for m in corpus if m.get("source_type") in PRIMARY_KINDS)
    international = sum(1 for m in corpus if m.get("source_type") == "report"
                        and "worldbank" in str(m.get("platform") or ""))
    by_record = {k: sum(1 for m in corpus if m.get("source_type") == k) for k in PRIMARY_KINDS}
    reactions = sum(1 for m in corpus if m.get("source_type") == "comment")
    video = sum(1 for m in corpus if (m.get("platform") or "").lower() in ("youtube", "tiktok"))
    dated = sum(1 for m in corpus if m.get("posted_at") and not m.get("date_estimated"))

    def pct(n, of):
        return round(100 * n / of, 1) if of else None

    return {"items": items, "by_kind": kinds, "by_segment": segments,
            "kenyan_outlets": len(kenyan_outlets), "articles": len(articles),
            "articles_full_text": full, "full_text_share": pct(full, len(articles)),
            "primary_records": primary, "records_by_kind": {k: v for k, v in by_record.items() if v},
            "international_records": international,
            "reactions": reactions, "reaction_share": pct(reactions, items),
            "video_share": pct(video, items), "dated_share": pct(dated, items),
            "pools": {k: sum(1 for m in corpus if m.get("evidence_pool") == k)
                      for k in ("core", "principal_side", "issue_side")}}


def page_support(payload: dict) -> dict:
    """Client-facing sentences by what they rest on."""
    it = payload.get("intersection") or {}
    unchecked = sum(_count(it.get(k)) for k in UNCHECKED)
    unchecked += sum(_count(s.get("detail")) for s in it.get("sub_issues") or [] if isinstance(s, dict))
    unchecked += sum(_count(n.get("summary")) for n in it.get("linking_narratives") or [] if isinstance(n, dict))

    on_record = 0
    cr = payload.get("connection_record") or {}
    if cr.get("state") == "measured":
        on_record += _count(cr.get("lead")) + len(cr.get("findings") or [])
    sc = payload.get("scenarios") or {}
    if sc.get("state") == "modelled":
        on_record += sum(_count(d.get("reading")) for d in sc.get("drivers") or [])
        for s in sc.get("scenarios") or []:
            on_record += sum(_count(x.get("text")) for part in ("factors", "outlook", "implications")
                             for x in s.get(part) or [])
        on_record += sum(_count(r.get("rationale")) or 1 for r in sc.get("recommendations") or [])
    total = on_record + unchecked
    return {"on_record": on_record, "unchecked": unchecked,
            "record_share": round(100 * on_record / total, 1) if total else None}


def removed(payload: dict) -> dict:
    sc = payload.get("scenarios") or {}
    def n(value) -> int:
        return len(value) if isinstance(value, (list, tuple)) else int(value or 0)

    return {"grounding": n((payload.get("grounding") or {}).get("removed")),
            "identity": n((payload.get("protected_terms") or {}).get("removed"))
            + n((sc.get("protected_terms") or {}).get("removed")),
            "scenario_check": len(((sc.get("check") or {}).get("removed")) or []),
            "press_set_aside": len(((payload.get("intersection") or {}).get("media_set_aside")) or [])}


def headline(read: dict, support: dict, record: dict) -> str:
    bits = [f"{read['items']} item(s) read"]
    if read["items"]:
        bits.append(f"{read['kenyan_outlets']} Kenyan outlet(s)")
        bits.append(f"{read['primary_records']} official record(s)")
        if read["video_share"] is not None and read["video_share"] >= 50:
            bits.append(f"{read['video_share']:g}% video")
    bits.append(f"{read['pools']['core']} naming both")
    bits.append(f"{record.get('findings_total', 0)} verified statement(s) linking them")
    if support["record_share"] is not None:
        bits.append(f"{support['record_share']:g}% of the page on the record")
    return "; ".join(bits) + "."


def assess(payload: dict, corpus: list[dict]) -> dict:
    """The map's quality record. Never raises: a measurement that fails is
    said, never mistaken for a map with nothing wrong."""
    try:
        read = corpus_read(corpus or [])
        support = page_support(payload)
        cr = payload.get("connection_record") or {}
        record = {"state": cr.get("state"), "findings_total": cr.get("findings_total", 0),
                  "principal_findings": cr.get("principal_findings", 0),
                  "co_mentioned": cr.get("co_mentioned"),
                  "scenarios": (payload.get("scenarios") or {}).get("state"),
                  "dated_next_steps": len([x for x in ((payload.get("issue_framework") or {})
                                                       .get("sequencing") or {}).get("engagement_timeline") or []
                                           if x.get("source") == "stated"])}
        acq = payload.get("acquisition") or {}
        social = acq.get("social_tier") or {}
        return {"measured": True, "kind": "issue_map", "version": VERSION,
                "commit": running_commit(), "measured_at": datetime.utcnow().isoformat(),
                "read": read, "relevance": {k: (acq.get("relevance_filter") or {}).get(k)
                                            for k in ("examined", "kept", "dropped", "pools")},
                "record": record, "page": support, "removed": removed(payload),
                "spend": {"socialcrawl_credits": social.get("credits_spent"),
                          "socialcrawl_budget": social.get("credit_budget")},
                "headline": headline(read, support, record)}
    except Exception as exc:  # noqa: BLE001
        return {"measured": False, "kind": "issue_map",
                "error": f"the map could not be measured: {type(exc).__name__}: {exc}"[:200]}
