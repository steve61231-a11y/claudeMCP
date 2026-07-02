"""Corpus-reading specialist analysts.

Unlike engine/reports/sections.py (which writes short sections from aggregate
statistics), each analyst here reads the actual mention texts — posts AND
comments, verbatim — and produces a structured, quote-grounded section. A final
synthesizer reads every analyst's output and writes the executive brief.

Grounding contract shared by every analyst:
- quotes must be verbatim from the supplied corpus and carry the mention `ref`
  id they came from; `_validate_quotes` drops any quote whose ref doesn't
  exist or whose text isn't actually found in that mention,
- no biographical/status facts from model memory (enforced by GROUNDING_RULES
  in every prompt and re-checked by `verify_grounding`).
"""

from datetime import datetime

from engine import llm

# Shared grounding preamble — the defense against stale-training-data claims
# (e.g. describing someone as alive/in office when they are not).
GROUNDING_RULES = """CRITICAL GROUNDING RULES:
- Use ONLY facts present in the provided source material.
- Do NOT add biographical facts, current statuses (alive/dead/in office/party
  membership), or events from your own knowledge — it may be out of date.
- Every quote you output must be copied VERBATIM from a source item and must
  include that item's ref id.
- If the sources don't support a claim, leave it out."""

ANALYST_MAX_TOKENS = 1500
CORPUS_CHARS_PER_CALL = 30000


def _render_mention(m: dict) -> str:
    eng = m.get("engagement") or {}
    eng_score = sum(int(eng.get(k) or 0) for k in ("views", "likes", "shares", "comments"))
    date = m.get("posted_at")
    date_s = date.date().isoformat() if isinstance(date, datetime) else str(date)[:10]
    ref = str(m.get("id", ""))[:8]
    text = (m.get("text") or "").strip().replace("\n", " ")
    return f"[ref={ref} | {m.get('platform')} {m.get('source_type')} | @{m.get('author_handle')} | {date_s} | engagement={eng_score}] {text}"


def _corpus_blob(mentions: list[dict], budget_chars: int = CORPUS_CHARS_PER_CALL) -> str:
    """Renders mentions into the text block an analyst reads. Comments are
    promoted (grassroots voice), then everything else by engagement, until the
    character budget is spent."""
    def eng(m):
        e = m.get("engagement") or {}
        return sum(int(e.get(k) or 0) for k in ("views", "likes", "shares", "comments"))

    comments = sorted((m for m in mentions if m.get("source_type") == "comment"), key=eng, reverse=True)
    posts = sorted((m for m in mentions if m.get("source_type") != "comment"), key=eng, reverse=True)
    # Interleave 1 comment : 2 posts so neither voice drowns the other.
    ordered: list[dict] = []
    ci, pi = 0, 0
    while ci < len(comments) or pi < len(posts):
        if ci < len(comments):
            ordered.append(comments[ci]); ci += 1
        ordered.extend(posts[pi:pi + 2]); pi += 2

    lines, used = [], 0
    for m in ordered:
        line = _render_mention(m)
        if used + len(line) > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _validate_quotes(quotes: list, mentions_by_ref: dict[str, dict]) -> list[dict]:
    """Keeps only quotes that are traceable: the ref exists and the quoted
    text actually appears in that mention (normalized substring)."""
    valid = []
    for q in quotes or []:
        if not isinstance(q, dict):
            continue
        ref = str(q.get("ref") or "")[:8]
        src = mentions_by_ref.get(ref)
        if not src:
            continue
        quoted = " ".join(str(q.get("text") or "").split()).lower()
        source_text = " ".join((src.get("text") or "").split()).lower()
        if not quoted or quoted[:60] not in source_text:
            continue
        valid.append(
            {
                "text": q.get("text"),
                "author": src.get("author_handle"),
                "platform": src.get("platform"),
                "source_type": src.get("source_type"),
                "url": src.get("source_url"),
                "ref": ref,
            }
        )
    return valid


def _refs(mentions: list[dict]) -> dict[str, dict]:
    return {str(m.get("id", ""))[:8]: m for m in mentions}


PUBLIC_VOICE_PROMPT = """You are a political intelligence analyst. Your job: report WHAT PEOPLE ARE ACTUALLY SAYING about {name}, in their own words, from the scraped posts and comments below.

{grounding}

Group what you find into three stances: supportive, critical, and neutral/questioning. For each stance identify the 2-4 dominant things people are saying (themes), and back EVERY theme with 2-4 verbatim quotes (exact text from a source item, with its ref id). Prefer comments over posts where available — comments are ordinary citizens' voices. Note the language/tone (English/Swahili/Sheng, mockery, praise, anger) where visible.

Required JSON shape:
{{"public_voice": {{"supportive": [{{"theme": "...", "summary": "...", "quotes": [{{"ref": "abcd1234", "text": "verbatim quote"}}]}}], "critical": [...], "neutral": [...]}}}}"""


def analyze_public_voice(name: str, mentions: list[dict]) -> dict:
    refs = _refs(mentions)
    result = llm.call_json_untrusted(
        PUBLIC_VOICE_PROMPT.format(name=name, grounding=GROUNDING_RULES),
        _corpus_blob(mentions),
        expected_keys={"public_voice"},
        max_tokens=2000,
        max_untrusted_chars=CORPUS_CHARS_PER_CALL,
    )
    voice = result["public_voice"]
    for stance in ("supportive", "critical", "neutral"):
        themes = voice.get(stance) or []
        for theme in themes:
            if isinstance(theme, dict):
                theme["quotes"] = _validate_quotes(theme.get("quotes"), refs)
        voice[stance] = [t for t in themes if isinstance(t, dict) and t.get("quotes")]
    return voice


PLATFORM_PULSE_PROMPT = """You are a political intelligence analyst describing what the conversation about {name} SOUNDS LIKE on each social platform, based on the scraped items below.

{grounding}

For each platform that appears with meaningful volume: describe the tone and dominant themes there, who the notable voices are, and give 2-3 verbatim example quotes (with ref ids). Do not just report counts — describe the conversation.

Required JSON shape:
{{"platform_pulse": [{{"platform": "tiktok", "tone": "...", "themes": ["..."], "notable_voices": ["@handle"], "quotes": [{{"ref": "abcd1234", "text": "..."}}]}}]}}"""


def analyze_platform_pulse(name: str, mentions: list[dict]) -> list[dict]:
    # Sample per platform so a dominant platform doesn't crowd out the rest.
    by_platform: dict[str, list[dict]] = {}
    for m in mentions:
        by_platform.setdefault(m.get("platform") or "?", []).append(m)
    top_platforms = sorted(by_platform.items(), key=lambda kv: len(kv[1]), reverse=True)[:6]
    sample: list[dict] = []
    for _, plat_mentions in top_platforms:
        sample.extend(plat_mentions[:20])
    refs = _refs(sample)
    result = llm.call_json_untrusted(
        PLATFORM_PULSE_PROMPT.format(name=name, grounding=GROUNDING_RULES),
        _corpus_blob(sample),
        expected_keys={"platform_pulse"},
        max_tokens=ANALYST_MAX_TOKENS,
        max_untrusted_chars=CORPUS_CHARS_PER_CALL,
    )
    pulse = [p for p in result["platform_pulse"] if isinstance(p, dict)]
    for p in pulse:
        p["quotes"] = _validate_quotes(p.get("quotes"), refs)
        p["mention_count"] = len(by_platform.get(p.get("platform"), []))
    return pulse


TIMELINE_PROMPT = """You are a political intelligence analyst reconstructing a dated timeline of the events that drove conversation about {name}, using the scraped items below (each item carries its date).

{grounding}

Identify the key dated moments (spikes in conversation) and for each: the date, what happened AS DESCRIBED IN THE SOURCES, and 1-2 verbatim quotes (with ref ids) showing the reaction. Order chronologically.

Required JSON shape:
{{"timeline": [{{"date": "YYYY-MM-DD", "event": "...", "quotes": [{{"ref": "abcd1234", "text": "..."}}]}}]}}"""


def analyze_timeline(name: str, mentions: list[dict], by_day: dict[str, int]) -> list[dict]:
    spike_days = {d for d, _ in sorted(by_day.items(), key=lambda kv: kv[1], reverse=True)[:10]}
    sample = [
        m for m in mentions
        if isinstance(m.get("posted_at"), datetime) and m["posted_at"].date().isoformat() in spike_days
    ] or mentions
    refs = _refs(sample)
    result = llm.call_json_untrusted(
        TIMELINE_PROMPT.format(name=name, grounding=GROUNDING_RULES),
        _corpus_blob(sample),
        expected_keys={"timeline"},
        max_tokens=ANALYST_MAX_TOKENS,
        max_untrusted_chars=CORPUS_CHARS_PER_CALL,
    )
    timeline = [t for t in result["timeline"] if isinstance(t, dict)]
    for t in timeline:
        t["quotes"] = _validate_quotes(t.get("quotes"), refs)
        t["mentions_that_day"] = by_day.get(str(t.get("date")), 0)
    return sorted(timeline, key=lambda t: str(t.get("date")))


INFLUENCER_STANCES_PROMPT = """You are a political intelligence analyst profiling the highest-influence accounts driving conversation about {name}. Below are their actual posts.

{grounding}

For each handle listed, describe: what kind of account they appear to be (from their content only), their stance toward {name} (supportive/critical/neutral/mixed), and what they've been saying — with 1-2 verbatim quotes (ref ids). Do not guess who runs an account.

Handles to profile: {handles}

Required JSON shape:
{{"influencer_stances": [{{"handle": "...", "account_type": "...", "stance": "...", "what_they_say": "...", "quotes": [{{"ref": "abcd1234", "text": "..."}}]}}]}}"""


def analyze_influencer_stances(name: str, mentions: list[dict], influence_summary: list[dict]) -> list[dict]:
    top_handles = [i["author_handle"] for i in influence_summary[:8]]
    sample = [m for m in mentions if m.get("author_handle") in set(top_handles)]
    if not sample:
        return []
    refs = _refs(sample)
    result = llm.call_json_untrusted(
        INFLUENCER_STANCES_PROMPT.format(name=name, grounding=GROUNDING_RULES, handles=", ".join(top_handles)),
        _corpus_blob(sample),
        expected_keys={"influencer_stances"},
        max_tokens=ANALYST_MAX_TOKENS,
        max_untrusted_chars=CORPUS_CHARS_PER_CALL,
    )
    stances = [s for s in result["influencer_stances"] if isinstance(s, dict)]
    score_by_handle = {i["author_handle"]: round(i["score"], 1) for i in influence_summary}
    for s in stances:
        s["quotes"] = _validate_quotes(s.get("quotes"), refs)
        s["influence_score"] = score_by_handle.get(s.get("handle"))
    return stances


NARRATIVE_DEEP_DIVE_PROMPT = """You are a political intelligence analyst writing a deep-dive on ONE storyline about {name}: "{label}" — {description}

Below are the actual posts/comments belonging to this storyline.

{grounding}

Write: how the story shows up in the sources (what concretely is being said/reported), who is pushing it (handles visible in the sources), how supporters frame it vs critics, and 3-5 verbatim quotes (ref ids) that best capture it.

Required JSON shape:
{{"deep_dive": {{"how_it_unfolded": "...", "who_is_driving_it": ["@handle"], "supporter_framing": "...", "critic_framing": "...", "quotes": [{{"ref": "abcd1234", "text": "..."}}]}}}}"""


def analyze_narrative_deep_dives(
    name: str, narratives: list[dict], mentions_by_id: dict[str, dict], top_n: int = 5
) -> list[dict]:
    dives = []
    ranked = sorted(narratives, key=lambda n: n.get("strength_score", 0), reverse=True)[:top_n]
    for n in ranked:
        members = [mentions_by_id[mid] for mid in n.get("mention_ids", []) if mid in mentions_by_id]
        if not members:
            continue
        refs = _refs(members)
        try:
            result = llm.call_json_untrusted(
                NARRATIVE_DEEP_DIVE_PROMPT.format(
                    name=name, label=n["label"], description=n["description"], grounding=GROUNDING_RULES
                ),
                _corpus_blob(members, budget_chars=15000),
                expected_keys={"deep_dive"},
                max_tokens=1200,
                max_untrusted_chars=16000,
            )
        except Exception:
            continue
        dive = result["deep_dive"]
        dive["label"] = n["label"]
        dive["mention_count"] = len(n.get("mention_ids", []))
        dive["quotes"] = _validate_quotes(dive.get("quotes"), refs)
        dives.append(dive)
    return dives


EXECUTIVE_BRIEF_PROMPT = """You are the lead political intelligence analyst. Your specialist team has each analyzed a different angle of the public conversation about {name}; their findings are below. Synthesize them into a decision-maker's brief.

{grounding}
Additionally: only restate claims already present in the specialist findings below.

Specialist findings:
{findings}

Write a 600-900 word executive brief a campaign strategist would actually use: what the public is saying and feeling (lead with this), the storylines that matter and where they're heading, who is shaping opinion for and against, the concrete moments that moved conversation, and what deserves action this week. Use specific quotes and handles from the findings. No generic advice.

Required JSON shape:
{{"executive_brief": "..."}}"""


def synthesize_executive_brief(name: str, analyst_outputs: dict) -> str:
    import json as _json

    findings = _json.dumps(analyst_outputs, default=str)[:45000]
    result = llm.call_json(
        EXECUTIVE_BRIEF_PROMPT.format(name=name, grounding=GROUNDING_RULES, findings=findings),
        max_tokens=2000,
    )
    return result.get("executive_brief", "")


VERIFY_PROMPT = """You are a fact-grounding auditor. Below is analyst-written report prose, followed by the source quotes the analysts worked from.

Your ONLY job: find sentences in the prose that assert a biographical fact or current status about a named person (alive/dead, holds office X, belongs to party Y, is a journalist at Z, etc.) that is NOT supported by the source quotes, and rewrite the prose with those unsupported claims removed. Do not add anything. Keep everything that IS supported or that makes no biographical/status claim.

Prose sections (JSON):
{sections}

Source quotes:
{quotes}

Required JSON shape (same keys as the input sections, values are the cleaned prose):
{{"cleaned": {{...}}}}"""


def verify_grounding(prose_sections: dict[str, str], source_quotes: list[str]) -> dict[str, str]:
    """One safety-net call: strips unsupported biographical/status claims from
    the free-prose sections. On any failure, returns the input unchanged."""
    import json as _json

    try:
        result = llm.call_json(
            VERIFY_PROMPT.format(
                sections=_json.dumps(prose_sections)[:30000],
                quotes="\n".join(source_quotes)[:15000],
            ),
            max_tokens=3000,
        )
        cleaned = result.get("cleaned")
        if isinstance(cleaned, dict):
            return {k: cleaned.get(k) or v for k, v in prose_sections.items()}
    except Exception:
        pass
    return prose_sections
