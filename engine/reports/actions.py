"""The worklist: what to do, about what, and whether it is moving.

The dashboard has had an "Actions in Progress" section since it was built.
It read `payload["actions"]`, then `payload["recommendations"]`, then
`sentiment_framework.strategic_implications.recommended_actions` — and
**none of those three has ever existed**. Nothing in this engine wrote any
of them. So on every live report ever produced, the section rendered "no
actions recorded for this period", which reads as a quiet week and was in
fact a section that was never wired up. That is this product's one
forbidden failure, in the part of the report that tells a client to do
something.

The client's note on it was: "this is a good section, we need to think how
the client would use it." This is that thinking.

**What an action is.** The analysts already produce the judgements — six to
twelve reputation risks and as many openings, each two to four sentences of
what it is, what in the data shows it, and what acting on it would look
like. They are rendered as prose in "What to do about it". What was missing
was never the content; it was the three things that make prose into a
worklist:

  - **What it is about.** An action matched to the narrative it concerns
    carries that narrative's share of the conversation, so the list can be
    ordered by the size of the thing being addressed instead of by the
    order a model happened to emit.
  - **Whether it is new.** Matched against the previous report's worklist,
    so "in progress" means it was on the list last time and is still here.
  - **Whether it is working.** The matched issue's share then, against its
    share now. That is the only honest answer to "is this moving", and it
    is arithmetic over two stored numbers.

**What this deliberately does not do** is invent a completion percentage.
The page used to draw a progress bar at 50% for anything whose status was
"in_progress" and 15% for everything else — a number with no referent,
rendered as a measurement, in a product whose entire argument is that its
numbers can be checked. There is no percentage here. There is a state, and
a movement in the thing the action addresses, and where the previous report
is missing, both say so.
"""

from __future__ import annotations

from engine.reports import prose

#: How many make the worklist. More than this and it stops being a list of
#: things to do; the full analysis stays in "What to do about it", which is
#: where someone goes to read rather than to act.
MAX_ACTIONS = 10

#: A share of the conversation below which "this issue grew" is noise.
#: Two narratives at 0.4% and 0.9% are not a doubling of anything.
MIN_MOVEMENT_SHARE = 2.0

#: Movement in percentage points past which an issue counts as moving.
MOVEMENT_POINTS = 1.5


def _terms(text: str) -> set[str]:
    """The words that could tie an action to the issue it is about.

    Same vocabulary rules as everywhere else in this codebase: the
    narrative labeller's stopword list and its stemmer, imported rather
    than restated, because the one place this project keeps paying for the
    same bug twice is where it wrote the rule down twice.
    """
    from engine.intelligence.narratives import _STOPWORDS, _WORD_RE, stem

    return {
        stem(match.lower())
        for match in _WORD_RE.findall(text or "")
        if len(match) >= 4 and match.lower() not in _STOPWORDS
    }


def _narrative_shares(payload: dict) -> list[dict]:
    """Each narrative with its share of the conversation, largest first."""
    rows = []
    for narrative in payload.get("narrative_breakdown") or payload.get("narratives") or []:
        label = narrative.get("label")
        if not label:
            continue
        rows.append({
            "label": label,
            "mentions": int(narrative.get("mention_count")
                            or narrative.get("mentions") or 0),
            "terms": _terms(f"{label} {narrative.get('description') or ''}"),
        })
    total = sum(row["mentions"] for row in rows) or 1
    for row in rows:
        row["share"] = round(100 * row["mentions"] / total, 1)
    rows.sort(key=lambda r: r["mentions"], reverse=True)
    return rows


def _match_issue(text: str, narratives: list[dict]) -> dict | None:
    """Which narrative an action is about, or none.

    Scored on shared distinctive terms, and `None` is a real answer: an
    action about a platform or a process may not correspond to any
    narrative, and inventing a match would put a made-up size next to it.
    """
    terms = _terms(text)
    best, score = None, 0
    for narrative in narratives:
        overlap = len(terms & narrative["terms"])
        if overlap > score:
            best, score = narrative, overlap
    return best if score >= 2 else None


def _candidates(payload: dict) -> list[dict]:
    """The analysts' own judgements, as things to do."""
    out = []
    for key, kind, verb in (("risks", "defend", "Answer"),
                            ("opportunities", "advance", "Act on")):
        for item in payload.get(key) or []:
            text = item if isinstance(item, str) else (
                item.get("detail") or item.get("item") or item.get("label") or "")
            text = (text or "").strip()
            if not text:
                continue
            label = prose.short_label(text)
            out.append({
                "action": label,
                "why": text,
                "kind": kind,
                # What a reader does with it, said in one word rather than
                # left to be inferred from which column it was in.
                "posture": verb,
            })
    return out


def build(payload: dict, previous: dict | None = None) -> dict:
    """The worklist for this report, and what changed since the last one."""
    narratives = _narrative_shares(payload)
    previous_narratives = {
        row["label"]: row["share"] for row in _narrative_shares(previous or {})
    }
    previous_actions = {
        (item.get("action") or "").lower(): item
        for item in ((previous or {}).get("actions") or {}).get("items") or []
    }

    items = []
    for candidate in _candidates(payload):
        issue = _match_issue(f"{candidate['action']} {candidate['why']}", narratives)
        before = previous_actions.get(candidate["action"].lower())
        row = {
            **candidate,
            "issue": issue["label"] if issue else None,
            "issue_share": issue["share"] if issue else None,
            "issue_mentions": issue["mentions"] if issue else None,
            # How many consecutive reports this has been on the list. One
            # means it is new, which is a different brief from an action
            # that four reports have carried without the issue shrinking.
            "runs": int((before or {}).get("runs") or 0) + 1,
            "status": "carried" if before else "new",
        }
        row.update(_movement(issue, previous, previous_narratives))
        items.append(row)

    # Ordered by the size of the thing addressed, then by posture: at equal
    # size a risk comes before an opening, because one is a fire and the
    # other is an option.
    items.sort(key=lambda r: (r["issue_share"] or 0, r["kind"] == "defend"),
               reverse=True)

    return {
        "items": items[:MAX_ACTIONS],
        "deferred": items[MAX_ACTIONS:],
        # What was on the list last time and is not now. Dropping it in
        # silence would let an action disappear because the issue was
        # settled and because the model forgot, and those are opposite
        # findings.
        "closed": sorted(
            {item.get("action") for item in previous_actions.values()}
            - {item["action"] for item in items}
        ) if previous_actions else [],
        "comparable": bool(previous),
        "note": (
            "Ordered by the share of the conversation each action addresses. "
            "There is no completion figure: a status and a movement in the "
            "underlying issue are the two things that can be checked."
            if previous else
            "Ordered by the share of the conversation each action addresses. "
            "This is the first report for this subject, so nothing can be "
            "reported as carried over or as moving yet."
        ),
    }


def _movement(issue: dict | None, previous: dict | None,
              previous_shares: dict[str, float]) -> dict:
    """Has the thing this action addresses grown or shrunk?

    Four distinct answers, and they are not interchangeable:

      `unknown`      no previous report — nothing to compare against
      `new_issue`    the previous report did not carry this narrative
      `too_small`    both readings are under the noise floor, where a
                     change of a few tenths of a point is not a finding
      a real delta   in percentage points of the conversation

    The temptation is to report 0.0 for the first three. That would claim
    an issue is steady on the strength of a measurement nobody made.
    """
    if issue is None:
        return {"movement": None, "movement_basis": "no_issue_matched"}
    if not previous:
        return {"movement": None, "movement_basis": "unknown"}
    before = previous_shares.get(issue["label"])
    if before is None:
        return {"movement": None, "movement_basis": "new_issue"}
    if max(before, issue["share"]) < MIN_MOVEMENT_SHARE:
        return {"movement": None, "movement_basis": "too_small"}
    delta = round(issue["share"] - before, 1)
    return {
        "movement": delta,
        "movement_basis": "measured",
        "movement_direction": ("growing" if delta >= MOVEMENT_POINTS
                               else "shrinking" if delta <= -MOVEMENT_POINTS
                               else "steady"),
    }
