"""What a storyline actually holds: its tone, who carried it, when, and who is in it.

The framework was handed each storyline as a label, a description and a
size, and asked to say what mattered. With nothing else to go on it chose
the biggest — and on a live report on a sitting deputy president the
biggest was "Playful reactions thread": emoji and laughter under a video,
chosen as the most important issue of the month. Under it, the page listed
"KEY PEOPLE: Ruto · null, William Ruto · President, Gachagua, Raila / Raila
Odinga, Rigathi Gachagua" — the subject himself twice, one man twice, and a
role that was the word "null" — and "ACCOUNTS AMPLIFYING IT: Facebook,
Daily Nation", which were the report's loudest sources, not the issue's.

Every one of those is a question the corpus can answer exactly, because each
storyline is a set of mentions and every mention carries its date, its
publisher, its tone and its text. This module answers them. Nothing here
calls a model or invents a figure: each number is a count over the
storyline's own items, and each judgement — that a storyline is chatter,
which way it leans — carries the counts it was made from.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from engine.reports import outlets

#: How many of a storyline's mentions must carry a tone before it is said to
#: lean either way. Fewer is an anecdote with a direction attached.
MIN_SCORED_FOR_LEAN = 3

#: A reaction counts for this much of a published item when storylines are
#: weighed against each other. A newsroom's article and a "😂😂" under a
#: video are not the same amount of coverage, and counting them alike is
#: how a comment thread outranked the news.
REACTION_WEIGHT = 0.25

#: Words a cluster's own label uses when it is not an issue at all — the
#: model that named "Playful reactions thread" was saying exactly that.
#: Deliberately NOT "reactions" alone: "Reactions to the impeachment ruling"
#: is an issue, and a rule that removes it removes the news.
_CHATTER = re.compile(
    r"\b(emojis?|laughter|laughing|laughs|playful|banter|greetings?|memes?|chatter|"
    r"jokes?|joking|lol|hype|small talk|off[- ]topic|spam|reaction (?:thread|emojis?)|"
    r"(?:fan|viewer|comment) (?:reactions|banter|chatter))\b", re.I)

#: Roles that say nothing, as the entity extractor writes them. "null" is
#: the model's JSON null that arrived as a string, printed to a client.
_EMPTY_ROLES = {"", "null", "none", "unknown", "n/a", "na", "other", "person",
                "individual", "not specified", "unspecified", "-"}

_URL = re.compile(r"https?://\S+")
_WORD = re.compile(r"[A-Za-zÀ-ɏ']{2,}")


def _id(value) -> str:
    return str(value) if value is not None else ""


def index_corpus(mentions: list[dict]) -> dict[str, dict]:
    """Corpus items by id — mentions only.

    A storyline's ids are RawMention ids. Documents ride in the same corpus
    with ids from another table, and an id is not unique across the two —
    see "article is not a table" in CLAUDE.md.
    """
    return {_id(m.get("id")): m for m in mentions
            if m.get("id") is not None and (m.get("kind") or "mention") == "mention"}


def sentiment_of(mention: dict, sentiments: dict | None) -> str | None:
    """The tone a mention was scored with, or None. Documents carry none:
    the scores are keyed by mention id, and a document sharing a number
    with a mention must not inherit its verdict."""
    if not sentiments or (mention.get("kind") or "mention") != "mention":
        return None
    reading = sentiments.get(mention.get("id")) or sentiments.get(_id(mention.get("id"))) or {}
    tone = reading.get("sentiment")
    return tone if tone in ("positive", "negative", "neutral") else None


def lean(tone: dict) -> str | None:
    """Which way a set of scored items leans, or None if too few were
    scored to say. `contested` is a finding in its own right: both sides
    are present in force."""
    pos, neg, neu = tone.get("positive", 0), tone.get("negative", 0), tone.get("neutral", 0)
    scored = pos + neg + neu
    if scored < MIN_SCORED_FOR_LEAN:
        return None
    if neg > pos * 1.5 and neg >= 0.25 * scored:
        return "negative"
    if pos > neg * 1.5 and pos >= 0.25 * scored:
        return "positive"
    if pos + neg >= 0.4 * scored:
        return "contested"
    return "neutral"


def _words(text: str | None) -> int:
    return len(_WORD.findall(_URL.sub(" ", text or "")))


def _posted(m: dict) -> datetime | None:
    value = m.get("posted_at")
    if isinstance(value, datetime) and not m.get("date_estimated"):
        return value
    return None


def item_card(m: dict, sentiments: dict | None = None) -> dict:
    """One item, as a reader can check it."""
    posted = m.get("posted_at")
    return {
        "mention_id": m.get("id"),
        "publisher": outlets.publisher(m),
        "outlet_type": outlets.classify(m)[0],
        "platform": m.get("platform"),
        "source_type": m.get("source_type"),
        "url": m.get("source_url"),
        "posted_at": posted.isoformat() if hasattr(posted, "isoformat") else posted,
        "date_estimated": bool(m.get("date_estimated")),
        "sentiment": sentiment_of(m, sentiments),
        "excerpt": re.sub(r"\s+", " ", (m.get("text") or "")).strip()[:280],
    }


def _engagement(m: dict) -> int:
    eng = m.get("engagement") or {}
    return sum(int(eng.get(k, 0) or 0) for k in ("views", "likes", "shares", "comments"))


def _representative(items: list[dict], limit: int = 3) -> list[dict]:
    """The items that best show what a storyline is: published ones before
    reactions, one per publisher before a second from any, loudest first and
    newest breaking ties."""
    published = [m for m in items if m.get("source_type") != "comment"] or list(items)
    ranked = sorted(published, key=lambda m: (_engagement(m), _posted(m) or datetime.min),
                    reverse=True)
    chosen, seen = [], set()
    for m in ranked:
        who = outlets.publisher(m)
        if who in seen:
            continue
        seen.add(who)
        chosen.append(m)
        if len(chosen) == limit:
            return chosen
    for m in ranked:
        if m not in chosen:
            chosen.append(m)
            if len(chosen) == limit:
                break
    return chosen


def profile(narrative: dict, by_id: dict[str, dict], sentiments: dict | None,
            corpus_size: int) -> dict | None:
    """Everything the corpus says about one storyline, counted. None when
    none of its mentions are in hand — the caller then says it cannot tell,
    rather than describing an empty storyline."""
    items = [by_id[_id(i)] for i in (narrative.get("mention_ids") or []) if _id(i) in by_id]
    if not items:
        return None
    reactions = [m for m in items if m.get("source_type") == "comment"]
    published = [m for m in items if m.get("source_type") != "comment"]

    tone = {"positive": 0, "negative": 0, "neutral": 0}
    for m in items:
        t = sentiment_of(m, sentiments)
        if t:
            tone[t] += 1
    scored = sum(tone.values())

    segments = Counter(outlets.classify(m)[0] for m in items)
    publishers = Counter(outlets.publisher(m) for m in published)
    segment_of = {outlets.publisher(m): outlets.classify(m)[0] for m in published}

    dated = sorted((m for m in items if _posted(m)), key=_posted)
    by_day = Counter(_posted(m).date().isoformat() for m in dated)
    peak = None
    if by_day:
        day, count = max(by_day.items(), key=lambda kv: (kv[1], kv[0]))
        peak = {"date": day, "count": count}

    words = [_words(m.get("text")) for m in items]
    return {
        "mentions": len(items),
        "share_pct": round(100 * len(items) / corpus_size, 1) if corpus_size else None,
        "published": len(published),
        "reactions": len(reactions),
        "weight": round(len(published) + REACTION_WEIGHT * len(reactions), 2),
        "outlets": len(publishers),
        "tone": tone,
        "scored": scored,
        "lean": lean(tone),
        "segments": {k: segments.get(k, 0) for k in
                     (outlets.LOCAL, outlets.INTERNATIONAL, outlets.SOCIAL)},
        "publishers": [{"name": name, "count": count, "outlet_type": segment_of.get(name)}
                       for name, count in publishers.most_common(6)],
        "first_seen": _posted(dated[0]).date().isoformat() if dated else None,
        "last_seen": _posted(dated[-1]).date().isoformat() if dated else None,
        "peak": peak,
        "by_day": dict(sorted(by_day.items())),
        "dated": len(dated),
        "mean_words": round(sum(words) / len(words), 1) if words else 0,
        "representative": [item_card(m, sentiments) for m in _representative(items)],
        "_items": items,
    }


def is_chatter(narrative: dict, prof: dict | None) -> str | None:
    """Why a storyline is not an issue, or None if it is one.

    Two signals, both needed where both exist. The cluster's own label —
    the labeller named it "Playful reactions thread" — and its make-up:
    a thread of short reactions. A storyline whose label sounds light but
    whose items are mostly published coverage ("Memes mocking the Finance
    Bill", reported by four newsrooms) is news about memes, and stays.
    """
    label = f"{narrative.get('label') or ''}"
    named_as_chatter = bool(_CHATTER.search(label))
    if prof is None:
        return "named as chatter by its own label" if named_as_chatter else None
    mostly_reactions = prof["reactions"] >= 0.6 * prof["mentions"]
    if named_as_chatter and mostly_reactions:
        return (f"a reaction thread — {prof['reactions']} of its {prof['mentions']} "
                "items are comments, and its own label names it as chatter")
    if mostly_reactions and prof["published"] == 0 and prof["mean_words"] < 5:
        return (f"{prof['mentions']} short reactions averaging "
                f"{prof['mean_words']:g} words, with no published coverage")
    return None


# --- people -----------------------------------------------------------------

def subject_tokens(subject) -> set[str]:
    """Every word of the subject's name and aliases, for telling the subject
    apart from the people around them."""
    if subject is None:
        return set()
    names = [getattr(subject, "name", None) or ""]
    names += [str(a) for a in (getattr(subject, "aliases", None) or [])]
    return {w.lower() for n in names for w in re.findall(r"[\w'-]+", n) if len(w) > 2}


def _tokens(name: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[\w'-]+", name or "") if len(w) > 2}


def clean_role(role) -> str | None:
    text = str(role or "").strip()
    return None if text.lower() in _EMPTY_ROLES else text


def merge_people(people: list[dict], subject=None) -> list[dict]:
    """One row per person, never the subject, never a role of "null".

    "Ruto" and "William Ruto" are one man; "Raila" and "Raila Odinga" are
    another. A short name is folded into the one longer name that contains
    all of its words, and only when exactly one does — "Mike" beside "Mike
    Sonko" and "Mike Mbuvi" stays as it is, because a guess here puts one
    person's record under another's name.
    """
    own = subject_tokens(subject)
    rows = []
    for person in people or []:
        name = re.sub(r"\s+", " ", str(person.get("name") or "")).strip()
        toks = _tokens(name)
        if not name or not toks or not any(ch.isupper() for ch in name):
            continue
        if own and toks <= own:
            continue
        rows.append({"name": name, "tokens": toks, "variants": {name},
                     "role": clean_role(person.get("role")),
                     "affiliation": clean_role(person.get("affiliation")),
                     "co_mentions": int(person.get("co_mentions") or 0)})

    rows.sort(key=lambda r: (len(r["tokens"]), r["co_mentions"]), reverse=True)
    kept: list[dict] = []
    for row in rows:
        hosts = [k for k in kept if row["tokens"] <= k["tokens"]]
        if len(hosts) == 1:
            host = hosts[0]
            host["variants"] |= row["variants"]
            host["co_mentions"] += row["co_mentions"]
            host["role"] = host["role"] or row["role"]
            host["affiliation"] = host["affiliation"] or row["affiliation"]
            continue
        kept.append(row)
    kept.sort(key=lambda r: r["co_mentions"], reverse=True)
    return [{"name": r["name"], "variants": sorted(r["variants"]), "role": r["role"],
             "affiliation": r["affiliation"], "co_mentions": r["co_mentions"]} for r in kept]


def people_in(items: list[dict], people: list[dict], limit: int = 6) -> list[dict]:
    """Of the people this report found, the ones named in these items —
    counted per item, so a reader is told "named in 14 of 40" and can weigh
    it."""
    out = []
    for person in people:
        pattern = re.compile(r"\b(" + "|".join(re.escape(v) for v in person["variants"])
                             + r")\b", re.I)
        named = sum(1 for m in items if pattern.search(m.get("text") or ""))
        if named:
            out.append({**person, "named_in": named, "of": len(items)})
    out.sort(key=lambda p: (p["named_in"], p["co_mentions"]), reverse=True)
    return out[:limit]
