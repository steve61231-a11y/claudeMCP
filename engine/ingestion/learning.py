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

## What this does NOT learn

Whether the material was any good. `result_count` measures novelty, not
relevance — a query returning fifty off-topic rows outscores one returning
three on-topic ones. Attributing the relevance verdict back to the query
that fetched it needs `RawMention` to carry its task, which it does not.
Said plainly here rather than left for a reader to assume, because a
learning loop that quietly optimises for the wrong thing is worse than one
that does not learn.
"""

from __future__ import annotations

from engine.db.models import IngestionRun, IngestionTask

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


def recall(db, politician_id, source: str, lookback: int = LOOKBACK_RUNS) -> dict[str, dict]:
    """What previous runs found out about each query for this source.

    Keyed by the query exactly as it was asked, so the memory is joinable
    back to the tasks table by hand.
    """
    recent_runs = [
        row.id for row in (
            db.query(IngestionRun.id)
            .filter(IngestionRun.politician_id == politician_id)
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

    for entry in memory.values():
        entry["average"] = round(entry["new_mentions"] / entry["asked"], 2)
        entry["retired"] = (
            entry["empty_in_a_row"] >= EMPTY_BEFORE_RETIRED
            and entry["runs_ago"] < RETIREMENT_RUNS
        )
    return memory


def plan(candidates: list[str], memory: dict[str, dict], budget: int,
         always_first: str | None = None) -> dict:
    """Which of `candidates` to run, given what is remembered.

    Returns the queries and the reasoning, because a plan a reader cannot
    see the reason for is a plan they have to take on trust — and this one
    changes every run.
    """
    if budget <= 0 or not candidates:
        return {"queries": candidates[:max(0, budget)], "proven": [],
                "explored": [], "retired": [], "memory_size": len(memory)}

    head: list[str] = []
    if always_first and always_first in candidates:
        head.append(always_first)

    retired = [q for q in candidates
               if memory.get(q, {}).get("retired") and q not in head]

    # Proven: asked before and brought back something. Best first.
    proven = sorted(
        (q for q in candidates
         if q not in head
         and memory.get(q, {}).get("new_mentions", 0) > 0),
        key=lambda q: -memory[q]["average"],
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
    return "; ".join(parts)
