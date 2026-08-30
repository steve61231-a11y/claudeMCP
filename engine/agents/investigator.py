"""Investigator agent — the stage that refuses to call the file finished.

Every other stage answers "what do we know?". This one asks the question an
analyst asks when they put a report down: **what are we missing?**

That matters because a fixed set of probe queries, however broad, can only find
what someone anticipated. The things that actually reshape a conclusion are
usually discovered *from within the evidence*: a company named once in passing, a
case number nobody followed, a person who keeps appearing beside the subject
with no explanation. Following those leads is how an investigation deepens
rather than merely repeats.

So this agent reads the finished state of the file — its claims, gaps, entities
and unexplained relationships — and produces two things:

  1. an **investigation agenda** for the human: what is thin, what is
     uncorroborated, what deserves a look and why,
  2. **new search queries** derived from what was actually found, fed back into
     discovery so the next run chases leads instead of re-running the same
     generic sweep.

That feedback loop is what makes the system iterative. Each run makes the next
run better informed.
"""

from datetime import datetime, timedelta

from engine import llm, stages
from engine.config import settings
from engine.db.models import Claim, Entity, EntityRelationship, Event

_MAX_LEADS = 12
_MAX_QUERIES = 15


AGENDA_PROMPT = """You are a senior intelligence analyst reviewing a case file on {subject}
before it goes to a client. Your job is NOT to summarise what is known — it is to
identify what is MISSING.

Current state of the file:

UNVERIFIED CLAIMS (asserted but not supported by stored evidence):
{unverified}

SINGLE-SOURCE EVENTS (reported, but no independent corroboration):
{single_source}

ENTITIES APPEARING WITHOUT EXPLANATION (named near the subject, relationship unclear):
{unexplained}

RECENT EVENTS:
{events}

Produce an investigation agenda. For each item state:
- question: the specific thing an analyst should establish next,
- why: what it would change about the conclusions if answered,
- priority: high | medium | low,
- suggested_query: a concrete web-search query that would help answer it
  (use quoted names, be specific — this is executed literally).

Prioritise by what would most change the picture, not by what is easiest.
Focus on gaps in the EVIDENCE, not on speculation about the subject.

Respond with ONLY this JSON:
{{"agenda": [{{"question": "...", "why": "...", "priority": "high", "suggested_query": "..."}}]}}"""


def _unverified_claims(db, politician, limit: int = 10) -> list[str]:
    rows = (
        db.query(Claim)
        .filter(Claim.politician_id == politician.id, Claim.status.in_(["unverified", "contradicted"]))
        .order_by(Claim.created_at.desc())
        .limit(limit)
        .all()
    )
    return [f"- {c.text[:200]} [{c.status}]" for c in rows]


def _single_source_events(db, politician, limit: int = 10) -> list[str]:
    rows = (
        db.query(Event)
        .filter(Event.politician_id == politician.id, Event.independent_domains <= 1)
        .order_by(Event.first_seen.desc().nullslast())
        .limit(limit)
        .all()
    )
    return [f"- {e.title[:200]}" for e in rows]


def _unexplained_entities(db, politician, limit: int = 12) -> list[str]:
    """Entities linked to the subject only by co-occurrence.

    These are the quiet leads: something put this name next to the subject, and
    nothing in the file says what.
    """
    rows = (
        db.query(Entity)
        .join(
            EntityRelationship,
            (EntityRelationship.source_entity_id == Entity.id)
            | (EntityRelationship.target_entity_id == Entity.id),
        )
        .filter(
            EntityRelationship.politician_id == politician.id,
            EntityRelationship.rel_type == "mentioned_with",
        )
        .order_by(Entity.mention_count.desc().nullslast())
        .limit(limit)
        .all()
    )
    seen: set[str] = set()
    out = []
    for entity in rows:
        if entity.name in seen:
            continue
        seen.add(entity.name)
        out.append(f"- {entity.name} ({entity.type}, seen {entity.mention_count or 0}x)")
    return out


def _recent_events(db, politician, limit: int = 10) -> list[str]:
    rows = (
        db.query(Event)
        .filter(Event.politician_id == politician.id)
        .order_by(Event.first_seen.desc().nullslast())
        .limit(limit)
        .all()
    )
    return [f"- {e.title[:160]} ({e.event_type or 'event'}, {e.independent_domains} source(s))" for e in rows]


def _fallback_agenda(db, politician) -> list[dict]:
    """A useful agenda even when the model is unavailable.

    Gaps are structural facts about the file — unverified claims and
    uncorroborated events are visible without a model, so an outage should
    degrade the wording, not the finding.
    """
    agenda: list[dict] = []
    for claim in (
        db.query(Claim)
        .filter(Claim.politician_id == politician.id, Claim.status == "unverified")
        .limit(5)
        .all()
    ):
        agenda.append(
            {
                "question": f"Find evidence for or against: {claim.text[:160]}",
                "why": "Asserted in the report but unsupported by stored evidence.",
                "priority": "high",
                "suggested_query": f'"{politician.name}" {" ".join(claim.text.split()[:6])}',
            }
        )
    for event in (
        db.query(Event)
        .filter(Event.politician_id == politician.id, Event.independent_domains <= 1)
        .limit(5)
        .all()
    ):
        agenda.append(
            {
                "question": f"Corroborate independently: {event.title[:160]}",
                "why": "Currently rests on a single source.",
                "priority": "medium",
                "suggested_query": f'"{politician.name}" {event.title[:60]}',
            }
        )
    return agenda


def build_agenda(db, politician) -> dict:
    """Produce the investigation agenda and the follow-up queries it implies."""
    unverified = _unverified_claims(db, politician)
    single_source = _single_source_events(db, politician)
    unexplained = _unexplained_entities(db, politician)
    events = _recent_events(db, politician)

    if not any((unverified, single_source, unexplained, events)):
        return {"agenda": [], "follow_up_queries": [], "note": "nothing in the file to investigate yet"}

    context = "\n".join(unverified + single_source + unexplained + events)
    agenda: list[dict] = []
    try:
        result = llm.call_json_untrusted(
            AGENDA_PROMPT.format(
                subject=politician.name,
                unverified="\n".join(unverified) or "(none)",
                single_source="\n".join(single_source) or "(none)",
                unexplained="\n".join(unexplained) or "(none)",
                events="\n".join(events) or "(none)",
            ),
            context,
            expected_keys={"agenda"},
            max_tokens=2000,
            max_untrusted_chars=len(context) + 1000,
        )
        for item in (result.get("agenda") or [])[:_MAX_LEADS]:
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            priority = str(item.get("priority") or "medium").lower()
            agenda.append(
                {
                    "question": question[:400],
                    "why": str(item.get("why") or "")[:400],
                    "priority": priority if priority in ("high", "medium", "low") else "medium",
                    "suggested_query": str(item.get("suggested_query") or "")[:200],
                }
            )
    except Exception as exc:  # noqa: BLE001
        stages.current().failed("investigator_agenda", exc)
        agenda = _fallback_agenda(db, politician)

    if not agenda:
        agenda = _fallback_agenda(db, politician)

    order = {"high": 0, "medium": 1, "low": 2}
    agenda.sort(key=lambda a: order.get(a["priority"], 1))

    queries = _derive_queries(politician, agenda, unexplained)
    return {"agenda": agenda, "follow_up_queries": queries}


def _derive_queries(politician, agenda: list[dict], unexplained: list[str]) -> list[str]:
    """Search queries generated from what was actually found.

    This is the loop closing: the next sweep pursues the specific leads this run
    surfaced, rather than repeating the same generic probe list.
    """
    queries: list[str] = []
    for item in agenda:
        query = (item.get("suggested_query") or "").strip()
        if query:
            queries.append(query)

    # Entities that appeared beside the subject with no explanation are worth a
    # direct look — pairing them with the subject is the cheapest way to find
    # what connects them.
    for line in unexplained[:6]:
        name = line.lstrip("- ").split(" (")[0].strip()
        if name and name.lower() != politician.name.lower():
            queries.append(f'"{politician.name}" "{name}"')

    seen: set[str] = set()
    unique = []
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique[:_MAX_QUERIES]


def store_follow_up_queries(db, politician, queries: list[str]) -> int:
    """Persist follow-up queries so the next run picks them up.

    Stored in their own column rather than among `keywords`: keywords are
    matching terms used for entity linking, and a lead is a question to ask —
    mixing them would corrupt matching.
    """
    if not queries:
        return 0
    from sqlalchemy.orm.attributes import flag_modified

    politician.investigation_leads = list(queries[:_MAX_QUERIES])
    flag_modified(politician, "investigation_leads")
    db.commit()
    return len(politician.investigation_leads)


def pending_leads(politician) -> list[str]:
    """Follow-up queries recorded by a previous run."""
    return list(politician.investigation_leads or [])
