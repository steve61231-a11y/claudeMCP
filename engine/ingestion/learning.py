"""What the last runs learned about this subject, fed into the next one.

The brief was "the more it keeps running these reports, the better it gets".
The pieces for that were already here and not joined up.

`IngestionTask` records, for every run: the connector, the exact query, and
`result_count` — the number of rows that were actually inserted. Because the
store is `ON CONFLICT DO NOTHING` on a content hash, that count is **new,
non-duplicate** mentions. A query that returns only what other queries
already found scores zero, which is exactly right: it taught us nothing.

Nobody had ever read those rows back. Every run asked a blind rotating slice
of the same probe list, so run twenty asked the same eighteen questions that
had returned nothing nineteen times, and the two that always work competed
for the same slots as the rest.

## What is learned

Per (subject, source, query): how many runs asked it, and how much new
material it brought back. That is the whole memory. It is deliberately not a
model, not an embedding and not a score nobody can check — an operator can
read it off the tasks table, and `explain()` prints it.

## Explore and exploit, and why the split is fixed

Asking only what worked is how a system stops discovering. A subject whose
court record has never surfaced will never have it surfaced again, because
the query that would find it has been out-competed by the one that reliably
returns press releases. So a fixed share of every budget goes to queries
this subject has never been asked, and that share cannot be optimised away.

## Why retirement decays

A query that has come back empty twice is treated as an answered question
and set aside — but only for a while. The world moves: a subject with no
court record this year may have one next year, and a memory that retires a
question permanently would guarantee we never find out. Retirement lifts
after `RETIREMENT_RUNS`, so the question is asked again periodically for as
long as the subject is tracked.

## Novelty is not quality, so both are measured

`result_count` counts rows, and a query returning fifty off-topic rows
would outscore one returning three on-topic ones. So the loop also asks how
much of what a query brought back turned out to be **about the subject** —
which is answerable, because entity linking writes a `MentionEntity` row
joining a mention to the subject's entity, and a mention now records the
task that fetched it.

Ranking is on linked mentions, with novelty as the tie-break. A query that
reliably returns material nobody can connect to the subject is a query that
wastes a slot, however much of it there is.

For subjects whose history predates mention-level task provenance the
linked count is unknown, not zero — `ranked_by` says which signal was
actually used, because a loop silently falling back to the weaker one while
appearing to use the stronger one is the kind of thing this codebase makes
a point of refusing to do.
"""

from __future__ import annotations

from engine.db.models import (Entity, IngestionRun, IngestionTask,
                              MentionEntity, RawMention)

#: How many of this subject's past runs to look at. Far enough back to see a
#: pattern, near enough that a question answered a year ago does not hold a
#: slot against this quarter's.
LOOKBACK_RUNS = 12

#: The most of any budget that may go to queries chosen because they worked
#: before. The rest is reserved for questions this subject has never been
#: asked — see the module docstring: a loop that only exploits stops
#: discovering, and the subject it stops discovering about is the one whose
#: record has not surfaced yet.
EXPLOIT_SHARE = 0.5

#: Empty results before a query is treated as an answered question. One is
#: noise — a source has an outage, a window happens to be quiet.
EMPTY_BEFORE_RETIRED = 2

#: How many runs a retirement lasts. The world moves.
RETIREMENT_RUNS = 6


def recall(db, politician, source: str, lookback: int = LOOKBACK_RUNS) -> dict[str, dict]:
    """What previous runs found out about each query for this source.

    Keyed by the query exactly as it was asked, so the memory is joinable
    back to the tasks table by hand.
    """
    recent_runs = [
        row.id for row in (
            db.query(IngestionRun.id)
            .filter(IngestionRun.politician_id == politician.id)
            .order_by(IngestionRun.started_at.desc().nullslast(),
                      IngestionRun.id.desc())
            .limit(lookback)
            .all()
        )
    ]
    if not recent_runs:
        return {}

    memory: dict[str, dict] = {}
    rows = (
        db.query(IngestionTask)
        .filter(IngestionTask.run_id.in_(recent_runs),
                IngestionTask.connector == source)
        .all()
    )
    # Oldest first, so `runs_ago` counts correctly and the newest result is
    # the one that decides a retirement.
    order = {run_id: index for index, run_id in enumerate(recent_runs)}
    rows.sort(key=lambda task: -order.get(task.run_id, 0))

    for task in rows:
        if not task.query:
            continue
        # A failed task says nothing about the query. Counting it as an empty
        # result would retire a perfectly good question because a source was
        # down, which is this codebase's standing rule about telling a
        # failure apart from a finding.
        if task.status not in ("done",):
            continue
        entry = memory.setdefault(task.query, {
            "asked": 0, "new_mentions": 0, "empty_in_a_row": 0,
            "runs_ago": order.get(task.run_id, 0),
        })
        yielded = int(task.result_count or 0)
        entry["asked"] += 1
        entry["new_mentions"] += yielded
        entry["empty_in_a_row"] = 0 if yielded else entry["empty_in_a_row"] + 1
        entry["runs_ago"] = min(entry["runs_ago"], order.get(task.run_id, 0))

    # How much of what each query returned was actually about the subject.
    #
    # Entity linking writes a MentionEntity row joining a mention to the
    # subject's own entity, and a mention records the task that fetched it,
    # so this is one join. It is the difference between learning "this query
    # returns rows" and "this query returns material we can use".
    linked = _linked_by_query(db, politician, recent_runs, source)

    for query, entry in memory.items():
        entry["average"] = round(entry["new_mentions"] / entry["asked"], 2)
        entry["linked"] = linked.get(query)
        entry["retired"] = (
            entry["empty_in_a_row"] >= EMPTY_BEFORE_RETIRED
            and entry["runs_ago"] < RETIREMENT_RUNS
        )
    return memory


def _linked_by_query(db, politician, run_ids: list[str], source: str) -> dict[str, int]:
    """Mentions per query that entity linking tied to THIS SUBJECT.

    The subject's own entity, not any entity. A mention linked to "Ministry
    of Health" is not evidence that a query about Mudavadi worked, and
    counting it would teach the loop that the noisiest queries are the best
    ones — which is the exact failure this function exists to prevent, so it
    is worth being careful about rather than clever.

    `Entity` is keyed by `canonical_key`, built as "type:lowercased name" by
    the pipeline, so the subject's entity is found the same way the pipeline
    finds it rather than by a join nothing supports.

    Returns an empty mapping rather than zeros when nothing is attributable:
    for a subject whose stored mentions predate task provenance the answer is
    unknown, and "unknown" and "none" must not be the same number.
    """
    try:
        key = f"politician:{(politician.name or '').strip().lower()}"
        entity = db.query(Entity.id).filter(Entity.canonical_key == key).first()
        if entity is None:
            return {}
        rows = (
            db.query(IngestionTask.query, MentionEntity.mention_id)
            .join(RawMention, RawMention.task_id == IngestionTask.id)
            .join(MentionEntity, MentionEntity.mention_id == RawMention.id)
            .filter(IngestionTask.run_id.in_(run_ids),
                    IngestionTask.connector == source,
                    RawMention.politician_id == politician.id,
                    RawMention.is_spam == 0,
                    MentionEntity.entity_id == entity.id)
            .all()
        )
        counts: dict[str, set] = {}
        for query, mention_id in rows:
            counts.setdefault(query, set()).add(mention_id)
        return {query: len(ids) for query, ids in counts.items()}
    except Exception:  # noqa: BLE001 — a memory must never break a run
        # A schema that predates the task column, or a join that cannot be
        # built. Falling back to novelty is a worse signal, not a broken run,
        # and `ranked_by` reports which one was used.
        return {}


def plan(candidates: list[str], memory: dict[str, dict], budget: int,
         always_first: str | None = None) -> dict:
    """Which of `candidates` to run, given what is remembered.

    Returns the queries and the reasoning, because a plan a reader cannot
    see the reason for is a plan they have to take on trust — and this one
    changes every run.
    """
    if budget <= 0 or not candidates:
        return {"queries": candidates[:max(0, budget)], "proven": [],
                "explored": [], "retired": [], "memory_size": len(memory),
                "ranked_by": "nothing to rank"}

    head: list[str] = []
    if always_first and always_first in candidates:
        head.append(always_first)

    retired = [q for q in candidates
               if memory.get(q, {}).get("retired") and q not in head]

    # Proven: asked before and brought back something. Best first.
    #
    # Ranked on mentions that entity linking tied to the subject, with
    # novelty as the tie-break. A query that reliably returns material
    # nobody can connect to the subject is a query wasting a slot, however
    # much of it there is — and ranking on row count alone would promote
    # exactly that query, because noise is plentiful.
    attributable = any(memory.get(q, {}).get("linked") is not None for q in candidates)
    ranked_by = "on-topic mentions" if attributable else "new mentions"

    def _worth(query: str) -> tuple[float, float]:
        entry = memory[query]
        linked = entry.get("linked")
        return (-(linked if linked is not None else 0), -entry["average"])

    proven = sorted(
        (q for q in candidates
         if q not in head
         and (memory.get(q, {}).get("linked") or 0) > 0
         or (q not in head and not attributable
             and memory.get(q, {}).get("new_mentions", 0) > 0)),
        key=_worth,
    )
    exploit_slots = max(0, int((budget - len(head)) * EXPLOIT_SHARE))
    proven = proven[:exploit_slots]

    # Exploration: never asked. Retired questions are not explored — they
    # are answered — but they are not candidates for exploitation either, so
    # a retired query simply does not run this time.
    untried = [q for q in candidates
               if q not in head and q not in proven and q not in retired
               and q not in memory]

    # Anything left: asked before, brought back nothing yet, not retired.
    # Last in line rather than excluded, because one empty result is noise.
    tried_and_thin = [q for q in candidates
                      if q not in head and q not in proven
                      and q not in retired and q not in untried]

    queries: list[str] = []
    for group in (head, proven, untried, tried_and_thin):
        for query in group:
            if query not in queries:
                queries.append(query)
            if len(queries) >= budget:
                break
        if len(queries) >= budget:
            break

    return {
        "queries": queries[:budget],
        "proven": [q for q in proven if q in queries[:budget]],
        "explored": [q for q in untried if q in queries[:budget]],
        "retired": retired,
        "memory_size": len(memory),
        # Which signal actually decided the order. A loop that quietly falls
        # back to the weaker one while appearing to use the stronger one is
        # the kind of thing this codebase refuses to do.
        "ranked_by": ranked_by,
    }


def explain(decision: dict) -> str:
    """One line an operator can read in the run stats.

    The loop changes what the next run asks, so it has to be able to say
    what it changed and why. A brain nobody can inspect is indistinguishable
    from a random number generator having a good week.
    """
    if not decision.get("memory_size"):
        return "first run for this subject — nothing remembered yet, every query is new"
    parts = [f"{decision['memory_size']} queries remembered"]
    if decision.get("proven"):
        parts.append(f"{len(decision['proven'])} repeated because they returned material")
    if decision.get("explored"):
        parts.append(f"{len(decision['explored'])} never asked before")
    if decision.get("retired"):
        parts.append(f"{len(decision['retired'])} set aside as answered "
                     f"(asked again after {RETIREMENT_RUNS} runs)")
    if decision.get("ranked_by"):
        parts.append(f"ranked by {decision['ranked_by']}")
    return "; ".join(parts)
