"""Evidence records — atomic, attributed, epistemically classified.

This is the inversion the pipeline needed. Today a model reads a digest of the
corpus, writes prose, and a later stage tries to fact-check that prose against
the corpus. Checking generated text after the fact cannot recover what the
generation already lost: which mention a sentence came from, and whether it was
a reported fact, somebody's allegation, or a stranger's opinion.

Here the extraction happens FIRST and at the level of the individual mention.
Every record carries the id of the mention it came from, so nothing downstream
can assert anything that does not trace back to a stored item. A synthesis
stage reading these can be constrained to them; a synthesis stage reading raw
text cannot be constrained to anything.

The epistemic class is the part that matters most and is easiest to get wrong.
"People are saying the project was abandoned" and "the project was abandoned"
are different statements, and a system that flattens the first into the second
is not summarising, it is fabricating.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from engine import stages
from engine import llm

# What a record asserts.
KIND_FACT = "fact"            # a stated occurrence, attributable to reporting
KIND_CLAIM = "claim"          # an allegation or assertion made BY someone
KIND_OPINION = "opinion"      # a stance or feeling, not a truth claim
KIND_EVENT = "event"          # something that happened, with a time
KIND_QUESTION = "question"    # an unresolved concern being raised
KINDS = (KIND_FACT, KIND_CLAIM, KIND_OPINION, KIND_EVENT, KIND_QUESTION)

# How well the corpus establishes it. This is about the EVIDENCE, not the
# extraction: `reported` means a source stated it, which is not the same as true.
STATUS_REPORTED = "reported"        # stated as fact by an identifiable source
STATUS_ALLEGED = "alleged"          # asserted by a party with an interest
STATUS_OPINION = "opinion"          # expressed as a view
STATUS_INFERRED = "inferred"        # the mention itself draws the conclusion
STATUS_UNRESOLVED = "unresolved"    # raised as open or disputed
STATUSES = (STATUS_REPORTED, STATUS_ALLEGED, STATUS_OPINION,
            STATUS_INFERRED, STATUS_UNRESOLVED)

_BATCH = 12
_WORKERS = 4


@dataclass
class EvidenceRecord:
    """One atomic thing a single mention says, and where it came from."""

    mention_id: str
    kind: str
    status: str
    statement: str
    topic: str = ""
    actor: str = ""             # who said or did it, per the mention
    sentiment: str = ""         # toward the subject: positive/neutral/negative
    quote: str = ""             # the words that carry it, verbatim
    platform: str | None = None
    author: str | None = None
    url: str | None = None
    posted_at: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


EXTRACT_PROMPT = """You are an intelligence analyst reading raw monitoring items about {subject}.

For EACH numbered item, extract the ATOMIC things it actually says. One item may
yield several records, or none. Extract only what the item itself states — never
what you know from elsewhere, and never a conclusion the item does not draw.

For every record give:
  "i"        — the item number it came from
  "kind"     — one of: fact | claim | opinion | event | question
                 fact     a stated occurrence reported as having happened
                 claim    an allegation or assertion made BY a named party
                 opinion  a stance, feeling or judgement
                 event    something that happened, tied to a time or place
                 question an unresolved concern or open question being raised
  "status"   — one of: reported | alleged | opinion | inferred | unresolved
                 reported   an identifiable source states it as fact
                 alleged    asserted by a party with a stake in it
                 opinion    expressed as a view, not a truth claim
                 inferred   the item itself reasons to this conclusion
                 unresolved raised as disputed, open, or unanswered
  "statement" — ONE sentence, self-contained, in English, naming who and what.
                Preserve attribution. Write "Governor X alleged that funds were
                diverted", NEVER "funds were diverted".
  "topic"    — 2-4 words naming the issue, as a newsroom would ("cost of living",
                "2027 coalition talks", "county funds audit")
  "actor"    — who states or performs it, if the item names them; else ""
  "sentiment" — toward {subject}: positive | neutral | negative
  "quote"    — the exact words from the item that carry it, verbatim, or ""

Rules that matter more than completeness:
  - NEVER promote an allegation to a fact. If the item says people are saying
    something, that is a claim with status alleged, not a fact.
  - If an item is pure engagement bait, a subscribe prompt, or says nothing
    about the subject, return no records for it.
  - Do not merge two items into one record. Every record belongs to ONE item.

Items:
{items}

Respond with ONLY this JSON:
{{"records": [{{"i": 1, "kind": "...", "status": "...", "statement": "...", "topic": "...", "actor": "...", "sentiment": "...", "quote": "..."}}]}}"""


def _normalise(entry: dict) -> tuple[str, str]:
    kind = str(entry.get("kind") or "").strip().lower()
    status = str(entry.get("status") or "").strip().lower()
    if kind not in KINDS:
        kind = KIND_CLAIM
    if status not in STATUSES:
        # Unknown provenance is never promoted. An unrecognised status becoming
        # "reported" is precisely how an allegation turns into a fact.
        status = STATUS_UNRESOLVED
    # A model that labels something a fact while calling it alleged is telling
    # us the weaker of the two; believe the weaker one.
    if kind == KIND_FACT and status in (STATUS_ALLEGED, STATUS_UNRESOLVED):
        kind = KIND_CLAIM
    return kind, status


def extract_batch(subject: str, mentions: list[dict]) -> list[EvidenceRecord]:
    """Extract records for one batch of mentions. Never raises; a failed batch
    splits, and a failed single mention yields nothing rather than guesses."""
    if not mentions:
        return []

    items = []
    for position, mention in enumerate(mentions, start=1):
        text = (mention.get("text") or "").strip()[:1800]
        items.append(f"[{position}] platform={mention.get('platform')} "
                     f"author={mention.get('author_handle')}\n{text}")

    prompt = EXTRACT_PROMPT.format(subject=subject, items="\n\n".join(items))
    try:
        reply = llm.call_json(
            prompt,
            max_tokens=min(llm.max_output_tokens(), 700 * len(mentions) + 800),
            model=llm.bulk_model(),
        )
        entries = reply.get("records") if isinstance(reply, dict) else None
        if not isinstance(entries, list):
            raise ValueError("records was not a list")
    except Exception as exc:  # noqa: BLE001
        if len(mentions) > 1:
            middle = len(mentions) // 2
            return (extract_batch(subject, mentions[:middle])
                    + extract_batch(subject, mentions[middle:]))
        # A single mention that yields nothing after splitting all the way down
        # contributes no evidence, and coverage would read that as a mention
        # with nothing in it rather than one we failed to read.
        stages.current().failed("evidence_extraction", exc)
        return []

    out: list[EvidenceRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(mentions):
            continue  # a record pointing at no item has no provenance; drop it
        statement = str(entry.get("statement") or "").strip()
        if not statement:
            continue
        mention = mentions[position - 1]
        kind, status = _normalise(entry)
        posted = mention.get("posted_at")
        out.append(EvidenceRecord(
            mention_id=mention.get("id"),
            kind=kind,
            status=status,
            statement=statement[:400],
            topic=str(entry.get("topic") or "").strip()[:60],
            actor=str(entry.get("actor") or "").strip()[:80],
            sentiment=str(entry.get("sentiment") or "").strip().lower()[:10],
            quote=str(entry.get("quote") or "").strip()[:400],
            platform=mention.get("platform"),
            author=mention.get("author_handle"),
            url=mention.get("source_url"),
            posted_at=posted.isoformat() if hasattr(posted, "isoformat") else posted,
        ))
    return out


def extract_records(subject: str, mentions: list[dict], limit: int | None = None) -> list[EvidenceRecord]:
    """Extract evidence records across the corpus, in batches, concurrently."""
    corpus = mentions[:limit] if limit else mentions
    if not corpus:
        return []
    batches = [corpus[i : i + _BATCH] for i in range(0, len(corpus), _BATCH)]
    workers = llm.concurrency(min(_WORKERS, len(batches)))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda b: extract_batch(subject, b), batches))
    return [record for batch in results for record in batch]


def coverage(records: list[EvidenceRecord], mentions: list[dict]) -> dict:
    """How much of the corpus produced evidence, and of what kind.

    Reported alongside every finding, because a claim built on records drawn
    from 8% of the corpus is a different object from one drawn from 80%."""
    with_records = {r.mention_id for r in records}
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for record in records:
        by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
        by_status[record.status] = by_status.get(record.status, 0) + 1
    return {
        "mentions_read": len(mentions),
        "mentions_yielding_evidence": len(with_records),
        "records": len(records),
        "by_kind": by_kind,
        "by_status": by_status,
    }


def to_json(records: list[EvidenceRecord]) -> str:
    return json.dumps([r.to_dict() for r in records], indent=2, default=str)
