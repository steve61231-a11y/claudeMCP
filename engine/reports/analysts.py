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

# How much an analyst may write, and how much corpus it gets to read.
#
# These were the binding constraint on report depth. At 2500 output tokens an
# analyst covering an entire corpus writes headline fragments — which is exactly
# what the issue map was returning. Depth is the product here, so the ceiling is
# the backend's, not an arbitrary number. Input is the cheaper half of the bill
# on every backend we use, so the read budget is generous too.
ANALYST_MAX_TOKENS = 8000
CORPUS_CHARS_PER_CALL = 60000
# Characters of whole-corpus digest a corpus-level analyst reads. The digest is
# already a compression of everything collected; truncating it again is where
# the second loss of detail happened.
DIGEST_CONTEXT_CHARS = 90000


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

Group what you find into three stances: supportive, critical, and neutral/questioning. For each stance identify EVERY distinct thing people are saying (themes) — typically 4-8 per stance where the corpus supports it, not two — and back EVERY theme with 3-6 verbatim quotes (exact text from a source item, with its ref id). Each theme's `summary` is 80-150 words: what the theme actually is, who holds it, how strongly, and how it is expressed. Prefer comments over posts where available — comments are ordinary citizens' voices. Note the language/tone (English/Swahili/Sheng, mockery, praise, anger) where visible.

Completeness matters as much as accuracy: a theme present in the corpus and missing from your output is a failure.

Required JSON shape — the example shows the FORM, not the QUANTITY. Return as many elements as the sources support, never as few as the example happens to show:
{{"public_voice": {{"supportive": [{{"theme": "...", "summary": "80-150 words...", "quotes": [{{"ref": "abcd1234", "text": "verbatim quote"}}, {{"ref": "efgh5678", "text": "another verbatim quote"}}, {{"ref": "ijkl9012", "text": "..."}}]}}, {{"theme": "...", "summary": "...", "quotes": [...]}}, {{"theme": "...", "summary": "...", "quotes": [...]}}, {{"theme": "...", "summary": "...", "quotes": [...]}}], "critical": [ ...same shape, 4-8 themes... ], "neutral": [ ...same shape... ]}}}}"""


def analyze_public_voice(name: str, mentions: list[dict]) -> dict:
    refs = _refs(mentions)
    result = llm.call_json_untrusted(
        PUBLIC_VOICE_PROMPT.format(name=name, grounding=GROUNDING_RULES),
        _corpus_blob(mentions),
        expected_keys={"public_voice"},
        max_tokens=ANALYST_MAX_TOKENS,
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

For each platform that appears with meaningful volume: describe the tone and dominant themes there, who the notable voices are, and give 3-5 verbatim example quotes (with ref ids). Do not just report counts — describe the conversation. `tone` is 100-200 words: what the conversation there actually sounds like, how it differs from the other platforms, who sets the register, and what a reader would notice first.

Required JSON shape — the example shows the FORM, not the QUANTITY. Return as many elements as the sources support, never as few as the example happens to show:
{{"platform_pulse": [{{"platform": "tiktok", "tone": "100-200 words...", "themes": ["...", "...", "..."], "notable_voices": ["@handle", "@handle2"], "quotes": [{{"ref": "abcd1234", "text": "..."}}, {{"ref": "efgh5678", "text": "..."}}, {{"ref": "ijkl9012", "text": "..."}}]}}, {{"platform": "x", "tone": "...", "themes": [...], "notable_voices": [...], "quotes": [...]}}, {{"platform": "news", "tone": "...", "themes": [...], "notable_voices": [...], "quotes": [...]}}]}}"""


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

Identify EVERY key dated moment (spikes in conversation) the sources support — typically 8-20 — and for each: the date, what happened AS DESCRIBED IN THE SOURCES, and 2-4 verbatim quotes (with ref ids) showing the reaction. Order chronologically.

Each `event` is a mini-briefing of 80-200 words, not a headline: what happened, who was involved, what was said, how people reacted, and why it mattered. Scale to significance — a pivotal day gets the full 200 words, a minor one 80 — but never a fragment.

Required JSON shape — the example shows the FORM, not the QUANTITY. Return as many elements as the sources support, never as few as the example happens to show:
{{"timeline": [{{"date": "YYYY-MM-DD", "event": "80-200 word mini-briefing...", "quotes": [{{"ref": "abcd1234", "text": "..."}}, {{"ref": "efgh5678", "text": "..."}}]}}, {{"date": "YYYY-MM-DD", "event": "...", "quotes": [...]}}, {{"date": "YYYY-MM-DD", "event": "...", "quotes": [...]}}, {{"date": "YYYY-MM-DD", "event": "...", "quotes": [...]}}, {{"date": "YYYY-MM-DD", "event": "...", "quotes": [...]}}, {{"date": "YYYY-MM-DD", "event": "...", "quotes": [...]}}, {{"date": "YYYY-MM-DD", "event": "...", "quotes": [...]}}, {{"date": "YYYY-MM-DD", "event": "...", "quotes": [...]}}]}}"""


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

For each handle listed, describe: what kind of account they appear to be (from their content only), their stance toward {name} (supportive/critical/neutral/mixed), and what they've been saying — with 2-4 verbatim quotes (ref ids). `what_they_say` is 80-180 words: their actual line of argument, how consistently they hold it, what they emphasise and what they omit. Do not guess who runs an account.

Handles to profile: {handles}

Required JSON shape — the example shows the FORM, not the QUANTITY. Return as many elements as the sources support, never as few as the example happens to show:
{{"influencer_stances": [{{"handle": "...", "account_type": "...", "stance": "...", "what_they_say": "80-180 words...", "quotes": [{{"ref": "abcd1234", "text": "..."}}, {{"ref": "efgh5678", "text": "..."}}]}}, {{"handle": "...", "account_type": "...", "stance": "...", "what_they_say": "...", "quotes": [...]}}, {{"handle": "...", "account_type": "...", "stance": "...", "what_they_say": "...", "quotes": [...]}}]}}"""


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

Write: how the story shows up in the sources (what concretely is being said/reported), who is pushing it (handles visible in the sources), how supporters frame it vs critics, and 4-8 verbatim quotes (ref ids) that best capture it.

Lengths: `how_it_unfolded` 250-500 words, tracing the storyline through the sources in order rather than summarising it. `supporter_framing` and `critic_framing` 80-150 words each, in the terms those sides actually use.

Required JSON shape — the example shows the FORM, not the QUANTITY. Return as many elements as the sources support, never as few as the example happens to show:
{{"deep_dive": {{"how_it_unfolded": "250-500 words...", "who_is_driving_it": ["@handle", "@handle2", "@handle3"], "supporter_framing": "80-150 words...", "critic_framing": "80-150 words...", "quotes": [{{"ref": "abcd1234", "text": "..."}}, {{"ref": "efgh5678", "text": "..."}}, {{"ref": "ijkl9012", "text": "..."}}, {{"ref": "mnop3456", "text": "..."}}]}}}}"""


def narrative_genealogy(members: list[dict]) -> dict:
    """Patient-zero tracing from timestamps alone (no LLM): where a storyline
    first appeared, how it jumped between platforms, and when it peaked."""
    dated = sorted(
        (m for m in members if isinstance(m.get("posted_at"), datetime)),
        key=lambda m: m["posted_at"],
    )
    if not dated:
        return {}
    first = dated[0]
    spread_path = []
    seen_platforms: set[str] = set()
    for m in dated:
        platform = m.get("platform") or "?"
        if platform not in seen_platforms:
            seen_platforms.add(platform)
            spread_path.append({"platform": platform, "date": m["posted_at"].date().isoformat()})
    by_day: dict[str, int] = {}
    for m in dated:
        key = m["posted_at"].date().isoformat()
        by_day[key] = by_day.get(key, 0) + 1
    peak_day = max(by_day.items(), key=lambda kv: kv[1])
    return {
        "first_seen": {
            "date": first["posted_at"].date().isoformat(),
            "author": first.get("author_handle"),
            "platform": first.get("platform"),
            "url": first.get("source_url"),
            "text": (first.get("text") or "")[:200],
        },
        "spread_path": spread_path,
        "peak": {"date": peak_day[0], "mentions": peak_day[1]},
        "platforms_reached": len(seen_platforms),
    }


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
                _corpus_blob(members, budget_chars=30000),
                expected_keys={"deep_dive"},
                max_tokens=4000,
                max_untrusted_chars=32000,
            )
        except Exception:
            continue
        dive = result["deep_dive"]
        dive["label"] = n["label"]
        dive["mention_count"] = len(n.get("mention_ids", []))
        dive["quotes"] = _validate_quotes(dive.get("quotes"), refs)
        dive["origin"] = narrative_genealogy(members)
        dives.append(dive)
    return dives


EXECUTIVE_BRIEF_PROMPT = """You are the lead political intelligence analyst. Your specialist team has each analyzed a different angle of the public conversation about {name}; their findings are below. Synthesize them into a decision-maker's brief.

{grounding}
Additionally: only restate claims already present in the specialist findings below.

Specialist findings:
{findings}

Write a 900-1500 word executive brief a campaign strategist would actually use: what the public is saying and feeling (lead with this), the storylines that matter and where they're heading, who is shaping opinion for and against, the concrete moments that moved conversation, and what deserves action this week. Use specific quotes and handles from the findings. No generic advice.

Required JSON shape:
{{"executive_brief": "..."}}"""


def synthesize_executive_brief(name: str, analyst_outputs: dict) -> str:
    import json as _json

    findings = _json.dumps(analyst_outputs, default=str)[:90000]
    result = llm.call_json(
        EXECUTIVE_BRIEF_PROMPT.format(name=name, grounding=GROUNDING_RULES, findings=findings),
        max_tokens=ANALYST_MAX_TOKENS,
    )
    return result.get("executive_brief", "")


INSIGHT_PROMPT = """You are a senior intelligence analyst. Below is a COMPLETE distilled digest of every mention collected about {name} — claims, themes, quotes, entities, sentiment and anomalies from the whole corpus. Your job is the part a human analyst does when, after reading everything, one thing suddenly clicks and the real picture snaps into focus.

{grounding}

Look BENEATH the surface. Do not restate the obvious headline story. Find:
- contradictions between what officials/media say and what ordinary people say;
- a narrative forming quietly under the dominant one;
- signs of coordinated or inauthentic amplification (many similar messages, sudden new voices);
- the single detail or connection that reframes how everything else should be read;
- what is conspicuously ABSENT that you would expect to see.

For each finding: state it plainly, explain the evidence pattern that supports it (referencing the digest), and give a confidence (high/medium/low). Only assert what the digest supports.

COMPLETE CORPUS DIGEST:
{digest}

Respond with ONLY this JSON:
{{"insights": [{{"headline":"the finding in one sharp sentence","reasoning":"the pattern in the data that reveals it","confidence":"high|medium|low","implication":"why it matters"}}], "the_one_thing":"if a decision-maker remembers only one non-obvious thing from all this data, it is: ..."}}"""


def analyze_deep_insights(name: str, corpus_digest: dict) -> dict:
    """The 'see through the layers' pass: reads the whole-corpus digest and
    surfaces non-obvious patterns, contradictions and the reframing insight."""
    from engine.reports.digest import digest_context

    try:
        result = llm.call_json(
            INSIGHT_PROMPT.format(
                name=name, grounding=GROUNDING_RULES, digest=digest_context(corpus_digest, max_chars=DIGEST_CONTEXT_CHARS)
            ),
            max_tokens=ANALYST_MAX_TOKENS,
        )
        insights = [i for i in (result.get("insights") or []) if isinstance(i, dict)]
        return {"insights": insights, "the_one_thing": result.get("the_one_thing", "")}
    except Exception:
        return {"insights": [], "the_one_thing": ""}


ISSUE_MAP_PROMPT = """You are an intelligence analyst mapping the relationship between a PRINCIPAL and an ISSUE/INSTITUTION. Below is a COMPLETE distilled digest of every collected mention at their intersection — everything said about {principal} *in connection with* {issue}.

{grounding}

COMPLETENESS IS ALSO A DUTY. Leaving out something the digest DOES support is as much a failure as inventing something it doesn't. Work through the digest systematically and account for everything in it. Do not summarise; map.

This output is read by someone who will act on it. A three-word timeline entry is useless to them. Write at the length the evidence justifies.

Map the intersection precisely:

- involvement: what is {principal}'s actual role, stance, action or exposure regarding {issue}? Say which (supporter/architect/critic/implicated/absent) and then evidence it. **300-600 words.** Cover what they have done, what they have said, how their position has moved, and where the record is contested.

- linking_narratives: every distinct storyline connecting {principal} to {issue} that the digest supports — typically **5-12**, not one. For each: the narrative, how it is framed, who pushes it, and (in `detail`) **100-250 words** on how it actually shows up in the sources.

- key_actors: EVERY person, organisation, agency, company, outlet or bloc that appears at this intersection — typically **15-40** when the digest is substantial, more if it supports more. Include the obvious principals AND the officials, regulators, contractors, county figures, MPs, critics, litigants, unions, journalists and commentators who appear. For each: `relation` in **40-120 words** (what they actually did or said here, not a job title), `entity_type` (person/organization/company), `position` on the issue ("for", "against" or "neutral" — use "neutral" unless the digest actually shows a stance), and `influence` 0-100 for how much they shape the outcome. Do not stop at four because the example below shows four.

- timeline: the sequence of notable moments linking them, oldest to newest — typically **10-30 entries**. Each `event` is a **mini-briefing of 80-200 words**: what happened, who was involved, what was said, how people reacted, and why it matters to {principal}. Scale to significance — a pivotal moment gets the full 200 words, a minor one can take 80 — but never write a headline fragment. Give `date` as ISO (YYYY-MM-DD) when the digest states or clearly implies one, otherwise null — never guess a date.

- tension_or_risk: where {principal} is exposed, contradicted, or where the narratives conflict. **200-400 words**, naming the specific contradictions.

- verdict: the single clearest read of how {principal} and {issue} are actually connected, beneath the headlines. **150-300 words.**

If the digest genuinely is thin, say so in `verdict` and return what it supports — but check first that it is thin, rather than assuming it.

COMPLETE INTERSECTION DIGEST:
{digest}

Respond with ONLY a JSON object of this shape. The example shows the FORM, not the QUANTITY — the arrays below are illustrative lengths, and yours should be as long as the digest supports:
{{"involvement":"300-600 words...",
"linking_narratives":[
 {{"narrative":"...","framing":"...","pushed_by":"...","detail":"100-250 words..."}},
 {{"narrative":"...","framing":"...","pushed_by":"...","detail":"..."}},
 {{"narrative":"...","framing":"...","pushed_by":"...","detail":"..."}},
 {{"narrative":"...","framing":"...","pushed_by":"...","detail":"..."}},
 {{"narrative":"...","framing":"...","pushed_by":"...","detail":"..."}}],
"key_actors":[
 {{"name":"...","relation":"40-120 words on what they did or said here...","entity_type":"person","position":"for","influence":85}},
 {{"name":"...","relation":"...","entity_type":"organization","position":"against","influence":70}},
 {{"name":"...","relation":"...","entity_type":"person","position":"neutral","influence":55}},
 {{"name":"...","relation":"...","entity_type":"company","position":"neutral","influence":40}},
 {{"name":"...","relation":"...","entity_type":"organization","position":"against","influence":35}},
 {{"name":"...","relation":"...","entity_type":"person","position":"for","influence":30}},
 {{"name":"...","relation":"...","entity_type":"person","position":"against","influence":25}},
 {{"name":"...","relation":"...","entity_type":"organization","position":"neutral","influence":20}}],
"timeline":[
 {{"when":"...","date":"YYYY-MM-DD or null","event":"80-200 word mini-briefing..."}},
 {{"when":"...","date":"YYYY-MM-DD or null","event":"..."}},
 {{"when":"...","date":"YYYY-MM-DD or null","event":"..."}},
 {{"when":"...","date":"YYYY-MM-DD or null","event":"..."}},
 {{"when":"...","date":"YYYY-MM-DD or null","event":"..."}},
 {{"when":"...","date":"YYYY-MM-DD or null","event":"..."}},
 {{"when":"...","date":"YYYY-MM-DD or null","event":"..."}},
 {{"when":"...","date":"YYYY-MM-DD or null","event":"..."}}],
"tension_or_risk":"200-400 words...",
"verdict":"150-300 words..."}}"""


def analyze_issue_intersection(principal: str, issue: str, corpus_digest: dict) -> dict:
    """Reads the whole intersection digest and maps how the principal and the
    issue/institution are actually connected. Degrades to an empty map."""
    from engine.reports.digest import digest_context

    empty = {"involvement": "", "linking_narratives": [], "key_actors": [],
             "timeline": [], "tension_or_risk": "", "verdict": ""}
    try:
        result = llm.call_json(
            ISSUE_MAP_PROMPT.format(
                principal=principal, issue=issue, grounding=GROUNDING_RULES,
                digest=digest_context(corpus_digest, max_chars=DIGEST_CONTEXT_CHARS),
            ),
            # The whole map is one object; every section above competes for this
            # budget. Give it everything the backend allows.
            max_tokens=llm.max_output_tokens(),
        )
    except Exception:
        return empty
    return {
        "involvement": result.get("involvement", ""),
        "linking_narratives": [n for n in (result.get("linking_narratives") or []) if isinstance(n, dict)],
        "key_actors": [a for a in (result.get("key_actors") or []) if isinstance(a, dict)],
        "timeline": [t for t in (result.get("timeline") or []) if isinstance(t, dict)],
        "tension_or_risk": result.get("tension_or_risk", ""),
        "verdict": result.get("verdict", ""),
    }


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
                sections=_json.dumps(prose_sections)[:60000],
                quotes="\n".join(source_quotes)[:30000],
            ),
            # It returns the prose it was given, so its budget must cover it —
            # a truncated reply here silently drops whole sections.
            max_tokens=llm.max_output_tokens(),
        )
        cleaned = result.get("cleaned")
        if isinstance(cleaned, dict):
            return {k: cleaned.get(k) or v for k, v in prose_sections.items()}
    except Exception:
        pass
    return prose_sections
