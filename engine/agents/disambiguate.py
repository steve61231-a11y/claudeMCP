"""Relevance & disambiguation gate — is this evidence actually about OUR subject?

Casting a wide net (metasearch over 200+ engines, dozens of probe queries) is
what makes discovery comprehensive. It is also what lets the wrong subject in:
names collide across people, places, companies and languages, and an acronym
like "SHA" means one thing in Kenyan health policy and something else entirely
elsewhere. Analysing that noise doesn't just waste tokens — it corrupts the
conclusions, which is worse than missing the data.

So breadth at acquisition is paired with precision at the gate:

  1. A **disambiguation profile** is derived from what the operator actually
     recorded about the subject (aliases, titles, affiliations, geography) —
     never invented, so every decision is auditable.
  2. **Deterministic checks first**: a document that never names the subject is
     rejected without spending a token; one that names them alongside several
     profile signals is accepted the same way. Most items resolve here.
  3. **The model adjudicates only the genuinely ambiguous middle**, in batches,
     on the cheap model.

Every verdict is stored with a score and a human-readable reason, so a wrongly
excluded document can be found and explained rather than vanishing silently.
"""

import re
from concurrent.futures import ThreadPoolExecutor

from engine import llm
from engine.config import settings
from engine.db.models import Document, Politician

# Verdicts written to Document.relevance_verdict.
ON_TOPIC = "on_topic"
OFF_TOPIC = "off_topic"
AMBIGUOUS = "ambiguous"

# Confidence bands for the deterministic pass. Anything between them is handed
# to the model rather than guessed at.
_ACCEPT_SCORE = 0.75
_REJECT_SCORE = 0.25

_MAX_SNIPPET = 700


def build_profile(politician: Politician) -> dict:
    """What we know about the subject, used to judge whether evidence matches.

    Built only from operator-entered fields so a disambiguation decision can
    always be traced back to a recorded fact about the subject.
    """
    name = (politician.name or "").strip()
    parts = name.split()
    return {
        "name": name,
        "surname": parts[-1] if parts else name,
        "aliases": [a for a in (politician.aliases or []) if a],
        "titles": [t for t in (politician.titles or []) if t],
        "keywords": [k for k in (politician.keywords or []) if k],
        "subject_type": getattr(politician, "subject_type", None) or "politician",
        # Signals that corroborate identity when the name alone is ambiguous.
        "context_terms": _context_terms(politician),
    }


def _context_terms(politician: Politician) -> list[str]:
    terms: list[str] = []
    terms.extend(politician.titles or [])
    terms.extend(politician.keywords or [])
    terms.extend(politician.swahili_terms or [])
    return [t.lower() for t in terms if t]


# A name this short is almost always an acronym or a common word, so a bare
# match means little on its own ("SHA" the health authority vs SHA-256 the hash).
_ACRONYM_LEN = 5


def _mentions(haystack: str, term: str) -> bool:
    """Whole-token match. Substring matching would count 'Mbadi' inside
    'Mbadinga' and 'SHA' inside 'SHA-256' — the precise errors this gate
    exists to prevent."""
    if not term:
        return False
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack) is not None


def score_document(text: str, title: str, profile: dict) -> tuple[float, str]:
    """Deterministic relevance score in [0,1] with the reason for it.

    Cheap, explainable, and correct for the clear-cut majority: full-name
    matches, name-plus-context matches, and pages that never mention the
    subject at all. Short/acronym subjects are held to a higher bar because a
    bare token match is genuinely uninformative for them.
    """
    haystack = f"{title or ''}\n{text or ''}".lower()
    if not haystack.strip():
        return 0.0, "empty document"

    name = (profile.get("name") or "").lower()
    surname = (profile.get("surname") or "").lower()
    aliases = [a.lower() for a in profile.get("aliases") or []]
    context = profile.get("context_terms") or []

    full_name_hit = _mentions(haystack, name)
    alias_hit = any(_mentions(haystack, a) for a in aliases)
    surname_hit = bool(surname) and len(surname) > 3 and _mentions(haystack, surname)
    context_hits = sum(1 for term in context if _mentions(haystack, term))

    if not (full_name_hit or alias_hit or surname_hit):
        return 0.0, "subject is never named"

    # An acronym/short name on its own proves nothing — demand corroboration.
    is_short_name = len(name.replace(" ", "")) <= _ACRONYM_LEN and " " not in name.strip()
    if is_short_name and full_name_hit and not alias_hit and context_hits == 0:
        return 0.3, f"'{name}' matched but nothing corroborates it — likely a different subject"

    # The full name plus any corroborating context is about as certain as a
    # keyword check gets.
    if full_name_hit and context_hits:
        return 0.95, f"full name + {context_hits} context signal(s)"
    if full_name_hit:
        return 0.8, "full name present"
    if alias_hit and context_hits:
        return 0.85, f"alias + {context_hits} context signal(s)"
    if alias_hit:
        return 0.6, "alias present, no corroborating context"
    # A bare surname is the classic homonym trap ("Kenyatta", "Odinga").
    if surname_hit and context_hits >= 2:
        return 0.7, f"surname + {context_hits} context signals"
    if surname_hit and context_hits == 1:
        return 0.5, "surname + 1 context signal"
    return 0.3, "surname only — could be a different subject"


ADJUDICATE_PROMPT = """You are disambiguating scraped documents for an intelligence file.

SUBJECT
  Name: {name}
  Type: {subject_type}
  Also known as: {aliases}
  Titles/roles: {titles}
  Associated context (org, place, domain): {context}

For EACH numbered document below decide whether it is genuinely about THIS
subject, or about a different person/organisation/thing that merely shares a
name, acronym or word.

Judge on evidence in the text: corroborating role, place, organisation, or
associated people. A shared name alone is NOT enough. A document that discusses
the subject in passing IS still about them (mark on_topic) — only mark off_topic
when it refers to a DIFFERENT entity, or has nothing to do with the subject.
When you genuinely cannot tell, say ambiguous rather than guessing.

Documents:
{batch}

Respond with ONLY this JSON:
{{"verdicts": [{{"i": 1, "verdict": "on_topic|off_topic|ambiguous", "confidence": 0.0-1.0, "reason": "short reason"}}]}}"""


def _adjudicate_batch(profile: dict, items: list[tuple[int, str, str]]) -> dict[int, dict]:
    """Ask the model about one batch of genuinely ambiguous documents."""
    lines = []
    for position, (_, title, text) in enumerate(items, start=1):
        snippet = (text or "")[:_MAX_SNIPPET].replace("\n", " ")
        lines.append(f"[{position}] TITLE: {title or '(none)'}\n     TEXT: {snippet}")
    batch = "\n".join(lines)

    try:
        result = llm.call_json_untrusted(
            ADJUDICATE_PROMPT.format(
                name=profile["name"],
                subject_type=profile["subject_type"],
                aliases=", ".join(profile["aliases"]) or "none recorded",
                titles=", ".join(profile["titles"]) or "none recorded",
                context=", ".join(profile["context_terms"][:12]) or "none recorded",
                batch=batch,
            ),
            batch,
            expected_keys={"verdicts"},
            max_tokens=1500,
            max_untrusted_chars=len(batch) + 1000,
            model=llm.bulk_model(),
        )
    except Exception:  # noqa: BLE001
        # A failed adjudication must not delete evidence: leave the batch
        # ambiguous so it is still analysed, just flagged.
        return {}

    out: dict[int, dict] = {}
    for verdict in result.get("verdicts") or []:
        try:
            position = int(verdict.get("i"))
        except (TypeError, ValueError):
            continue
        if 1 <= position <= len(items):
            out[items[position - 1][0]] = {
                "verdict": str(verdict.get("verdict") or AMBIGUOUS),
                "confidence": float(verdict.get("confidence") or 0.5),
                "reason": str(verdict.get("reason") or "")[:300],
            }
    return out


def gate_documents(db, politician: Politician, limit: int | None = None) -> dict:
    """Score and label every ungated document for this subject.

    Idempotent: only documents without a verdict are examined, so a re-run
    resumes rather than re-paying for work already done.
    """
    profile = build_profile(politician)
    query = (
        db.query(Document)
        .filter(Document.politician_id == politician.id, Document.relevance_verdict.is_(None))
        .order_by(Document.fetched_at.desc().nullslast())
    )
    if limit:
        query = query.limit(limit)
    documents = query.all()
    if not documents:
        return {"examined": 0, "on_topic": 0, "off_topic": 0, "ambiguous": 0, "adjudicated": 0}

    undecided: list[tuple[int, str, str]] = []
    by_index: dict[int, Document] = {}
    counts = {ON_TOPIC: 0, OFF_TOPIC: 0, AMBIGUOUS: 0}

    for index, doc in enumerate(documents):
        score, reason = score_document(doc.body or "", doc.title or "", profile)
        doc.relevance_score = score
        if score >= _ACCEPT_SCORE:
            doc.relevance_verdict = ON_TOPIC
            doc.relevance_reason = reason
            counts[ON_TOPIC] += 1
        elif score <= _REJECT_SCORE:
            doc.relevance_verdict = OFF_TOPIC
            doc.relevance_reason = reason
            counts[OFF_TOPIC] += 1
        else:
            by_index[index] = doc
            undecided.append((index, doc.title or "", doc.body or ""))

    adjudicated = 0
    if undecided:
        size = max(1, settings.agent_batch_size)
        batches = [undecided[i : i + size] for i in range(0, len(undecided), size)]
        workers = min(4, len(batches))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for verdicts in pool.map(lambda b: _adjudicate_batch(profile, b), batches):
                for index, verdict in verdicts.items():
                    doc = by_index.get(index)
                    if doc is None:
                        continue
                    label = verdict["verdict"]
                    if label not in (ON_TOPIC, OFF_TOPIC, AMBIGUOUS):
                        label = AMBIGUOUS
                    doc.relevance_verdict = label
                    doc.relevance_reason = f"model: {verdict['reason']}"
                    doc.relevance_score = verdict["confidence"]
                    counts[label] = counts.get(label, 0) + 1
                    adjudicated += 1

        # Anything the model didn't answer for stays analysable but flagged.
        for index, doc in by_index.items():
            if doc.relevance_verdict is None:
                doc.relevance_verdict = AMBIGUOUS
                doc.relevance_reason = "not adjudicated — kept for analysis"
                counts[AMBIGUOUS] += 1

    db.commit()
    return {
        "examined": len(documents),
        "on_topic": counts.get(ON_TOPIC, 0),
        "off_topic": counts.get(OFF_TOPIC, 0),
        "ambiguous": counts.get(AMBIGUOUS, 0),
        "adjudicated": adjudicated,
    }
