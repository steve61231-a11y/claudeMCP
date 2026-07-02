"""Message simulator: predict how the public will receive a draft statement,
grounded in how they ACTUALLY reacted to similar past statements.

Not a crystal ball — a pattern-matcher over documented cause-and-effect: we
embed the draft, retrieve the most similar stored mentions (posts + comments)
about the politician, and ask the LLM to project reception per audience
segment, with every prediction anchored to verbatim historical reactions.
"""

import numpy as np
from sqlalchemy.orm import Session

from engine import llm
from engine.db.models import MentionSentiment, Politician, RawMention
from engine.intelligence.narratives import embed_texts
from engine.reports.analysts import GROUNDING_RULES, _corpus_blob, _refs, _validate_quotes

MAX_CORPUS = 400
TOP_SIMILAR = 60

SIMULATE_PROMPT = """You are a political communications analyst. A draft message that {name}'s team is considering publishing is shown first. After it are REAL past public reactions (posts and comments) to {name}'s previous statements, selected for similarity to this draft.

{grounding}
Additional rule: predictions must be justified by patterns visible in the provided past reactions — cite them. If the past reactions don't support a prediction, say the reception is uncertain rather than guessing.

DRAFT MESSAGE UNDER TEST:
<draft>
{draft}
</draft>

Analyze how this draft is likely to be received, based ONLY on the reaction patterns below. Required JSON shape:
{{"simulation": {{
  "overall_read": "2-3 sentence bottom line",
  "segments": [{{"audience": "e.g. TikTok youth / LinkedIn professionals / party base", "predicted_reception": "positive|negative|mixed|uncertain", "why": "the pattern in past reactions that predicts this", "supporting_quotes": [{{"ref": "abcd1234", "text": "verbatim past reaction"}}]}}],
  "risky_lines": [{{"line": "phrase from the draft", "risk": "how it's likely to be twisted/mocked, per past patterns", "supporting_quotes": [{{"ref": "abcd1234", "text": "..."}}]}}],
  "suggestions": ["specific rewording grounded in what landed well before"]
}}}}"""


def simulate_message(db: Session, politician: Politician, draft_text: str) -> dict:
    rows = (
        db.query(RawMention, MentionSentiment)
        .outerjoin(MentionSentiment, MentionSentiment.mention_id == RawMention.id)
        .filter(RawMention.politician_id == politician.id, RawMention.is_spam == 0)
        .order_by(RawMention.posted_at.desc())
        .limit(MAX_CORPUS)
        .all()
    )
    mentions = [
        {
            "id": m.id,
            "platform": m.platform,
            "source_type": m.source_type,
            "author_handle": m.author_handle,
            "text": m.text,
            "posted_at": m.posted_at,
            "engagement": m.engagement_json or {},
            "source_url": m.source_url,
            "_sentiment": s.sentiment if s else None,
        }
        for m, s in rows
        if (m.text or "").strip()
    ]
    if len(mentions) < 10:
        return {"error": f"not enough stored data about {politician.name} to simulate against"}

    texts = [m["text"] for m in mentions]
    vectors = embed_texts([draft_text] + texts)
    draft_vec, corpus_vecs = vectors[0], vectors[1:]

    def cos(a, b):
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
        return float(np.dot(a, b) / denom)

    scored = sorted(zip(mentions, corpus_vecs), key=lambda pair: cos(draft_vec, pair[1]), reverse=True)
    similar = [m for m, _ in scored[:TOP_SIMILAR]]

    refs = _refs(similar)
    result = llm.call_json_untrusted(
        SIMULATE_PROMPT.format(name=politician.name, grounding=GROUNDING_RULES, draft=draft_text[:2000]),
        _corpus_blob(similar, budget_chars=20000),
        expected_keys={"simulation"},
        max_tokens=2500,
        max_untrusted_chars=22000,
    )
    sim = result["simulation"]
    for segment in sim.get("segments") or []:
        if isinstance(segment, dict):
            segment["supporting_quotes"] = _validate_quotes(segment.get("supporting_quotes"), refs)
    for risky in sim.get("risky_lines") or []:
        if isinstance(risky, dict):
            risky["supporting_quotes"] = _validate_quotes(risky.get("supporting_quotes"), refs)
    sim["based_on"] = {
        "reactions_considered": len(similar),
        "corpus_size": len(mentions),
    }
    return sim
