"""Sentiment Analysis Framework — V1.0.

A direct implementation of the client's framework document. The parameter
numbering, ordering and naming below mirror that document exactly, including
its own terminology ("potential levers", "potential barriers"), because the
framework is the deliverable's contract: an analyst reading our output should
recognise their own structure, not a reinterpretation of it.

Framework parameters:
  1.0 Summary of subject        — who they are (individuals AND companies),
                                  position (individuals), executive summary of
                                  findings in 3, 4 and 5.
  2.0 Sentiment score           — a headline number "for executives who don't
                                  like to read", plus the previous score.
  2.0 Overall mentions          — total over the reporting period, percentage
                                  difference from the previous period, and
                                  segmentation by outlet type.
  3.0 Sentiment                 — totals for positive / negative / neutral and
                                  the sentiment score (share of positive).
  4.0 Current issues            — up to 3 main positive issues (potential
                                  levers) and the main negative issues
                                  (potential barriers).
  5.0 Emergent issues           — high-impact coverage in the 72 hours before
                                  the reporting date, qualified by engagement.
  6.0 Strategic implications    — the most important issue: outline, status,
                                  trajectory, key dates and people.

Where the framework asks for something we cannot yet evidence, the section says
so rather than inventing it — a blank in a due-diligence report is information.
"""

import re
from datetime import datetime, timedelta

# The framework's own threshold for "high impact" on social media.
HIGH_ENGAGEMENT_THRESHOLD = 100
EMERGENT_WINDOW_HOURS = 72
MAX_CURRENT_ISSUES = 3  # "we can stick to 3 maximum"
MAX_EMERGENT_ITEMS = 10

# Channel-promotion boilerplate. These phrases are an advertisement for the
# uploader, not coverage of the subject, and the items carrying them are
# reliably the highest-engagement things in a Kenyan political corpus:
# "Ruto Finished", "Uhuru COmpletely Destroys Ruto", each wrapped in a plea to
# subscribe. Ranking on raw engagement handed every slot in the flagship
# section to them while 238 news items never appeared at all.
#
# This detects SELF-PROMOTION, not opinion. A furious editorial stays; a video
# whose description is mostly a pitch for its own channel is demoted.
_PROMO_MARKERS = (
    "subscribe", "join this channel", "get access to perks", "like and share",
    "hit the bell", "turn on notifications", "support our work",
    "follow us on", "click the link", "don't forget to",
)

# Outlet segmentation. The framework asks for local media / international media
# / social media, and notes the exact list is agreed with each client — so this
# is a starting classification, not a fixed taxonomy.
_SOCIAL_PLATFORMS = {
    "twitter", "x", "facebook", "instagram", "tiktok", "youtube", "linkedin",
    "reddit", "threads", "telegram", "whatsapp",
}
_LOCAL_TLDS = (".ke", ".co.ke", ".go.ke", ".or.ke", ".ac.ke")
_KNOWN_LOCAL = {
    "nation.africa", "standardmedia.co.ke", "the-star.co.ke", "citizen.digital",
    "kenyans.co.ke", "tuko.co.ke", "capitalfm.co.ke", "ntvkenya", "kbc.co.ke",
    "people.co.ke", "businessdailyafrica.com", "kahawatungu.com", "nairobiwire.com",
}


def classify_outlet(platform: str | None, domain: str | None = None) -> str:
    """local_media | international_media | social_media.

    Social is decided by platform; the local/international split is decided by
    domain, since an outlet's reach — not its subject — is what the segmentation
    is about.
    """
    key = (platform or "").strip().lower()
    if key in _SOCIAL_PLATFORMS:
        return "social_media"

    host = (domain or platform or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "international_media"
    if host in _KNOWN_LOCAL or any(host.endswith(tld) for tld in _LOCAL_TLDS):
        return "local_media"
    return "international_media"


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def sentiment_score(positive: int, negative: int, neutral: int) -> float:
    """The framework defines this as the share of positive mentions.

    Its author flagged it as provisional ("let me know if you have a better
    metric"), so it is computed exactly as specified and the definition is
    carried in the payload — a score whose meaning travels with it can be
    challenged; a bare number can't.
    """
    total = positive + negative + neutral
    return _pct(positive, total)


def subject_profile_from_corpus(mentions: list[dict]) -> str | None:
    """Who the subject actually is, taken from the reference material we hold.

    The Wikipedia connector fetches the subject's article and files it as
    source_type="reference" with relation="subject" — precisely so the report
    can say "President of Kenya" instead of "public figure". Nothing ever read
    it: `payload["subject_profile"]` was consumed here and written nowhere, so
    every auto-created subject was described as a "public figure" while their
    encyclopedia entry sat unread in the corpus.

    Takes the opening sentences, which is where an encyclopedia puts the
    identity. Returns None rather than a guess when no reference was collected.
    """
    for mention in mentions or []:
        if (mention.get("source_type") or "") != "reference":
            continue
        raw = mention.get("raw_payload") or {}
        if raw.get("relation") not in (None, "subject"):
            continue
        text = " ".join((mention.get("text") or "").split())
        if not text:
            continue
        # Two sentences is enough for an identity line and short enough to sit
        # in a header without pushing the numbers off the screen.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        profile = " ".join(sentences[:2]).strip()
        if len(profile) > 40:
            return profile[:400]
    return None


def build_summary_of_subject(politician, payload: dict) -> dict:
    """1.0 — who the subject is, their position, and an executive summary.

    The framework covers both individuals and companies, so 'position' is
    included only where it applies.
    """
    subject_type = getattr(politician, "subject_type", None) or "politician"
    is_person = subject_type in {"person", "politician", "individual"}

    titles = [t for t in (politician.titles or []) if t]
    return {
        "name": politician.name,
        "subject_type": subject_type,
        "who_they_are": payload.get("subject_profile")
        or _identity_line(politician, is_person, titles),
        "identity_source": "reference material" if payload.get("subject_profile") else "operator record",
        # Position applies to individuals only, per the framework.
        "position": (titles[0] if titles else None) if is_person else None,
        "executive_summary": payload.get("executiveBrief") or payload.get("summary") or "",
        "covers_parameters": ["3.0 Sentiment", "4.0 Current issues", "5.0 Emergent issues"],
    }


def _identity_line(politician, is_person: bool, titles: list[str]) -> str:
    affiliations = [k for k in (politician.keywords or []) if k]
    if is_person:
        role = titles[0] if titles else "public figure"
        context = f" ({', '.join(affiliations[:3])})" if affiliations else ""
        return f"{politician.name} — {role}{context}."
    return f"{politician.name} — organisation/company{(' (' + ', '.join(affiliations[:3]) + ')') if affiliations else ''}."


def sentiment_counts(payload: dict, sentiments: dict | None = None) -> tuple[int, int, int]:
    """Positive / negative / neutral counts.

    Counted from the stored per-mention records where available — that is the
    ground truth. The report payload carries only percentages, so counts are
    reconstructed from them as a fallback rather than left at zero.
    """
    if sentiments:
        tally = {"positive": 0, "negative": 0, "neutral": 0}
        for record in sentiments.values():
            label = (
                record.get("sentiment") if isinstance(record, dict) else str(record)
            )
            if label in tally:
                tally[label] += 1
        if any(tally.values()):
            return tally["positive"], tally["negative"], tally["neutral"]

    breakdown = payload.get("sentiment_breakdown") or {}
    total = int(breakdown.get("total_mentions_analyzed") or 0)
    if not total:
        return 0, 0, 0
    return (
        round(total * float(breakdown.get("positive_pct") or 0) / 100),
        round(total * float(breakdown.get("negative_pct") or 0) / 100),
        round(total * float(breakdown.get("neutral_pct") or 0) / 100),
    )


def build_sentiment_score_section(payload: dict, previous: dict | None,
                                  sentiments: dict | None = None) -> dict:
    """2.0 — the headline number, and what it was last period."""
    positive, negative, neutral = sentiment_counts(payload, sentiments)
    analysed = positive + negative + neutral
    # A headline "0.0%" off nothing scored is indistinguishable from a subject
    # with genuinely no positive coverage, and one of those is a finding while
    # the other is a failure.
    score = sentiment_score(positive, negative, neutral) if analysed else None

    previous_score = None
    if previous:
        previous_score = previous.get("sentiment_framework", {}).get("sentiment_score", {}).get("score")

    change = (round(score - previous_score, 1)
              if previous_score is not None and score is not None else None)
    return {
        "score": score,
        "definition": "Share of positive mentions over the reporting period (framework 3.0).",
        "scoring_gap": _scoring_gap(payload, analysed),
        "previous_score": previous_score,
        "change": change,
        "direction": (
            "improving" if change and change > 0 else "declining" if change and change < 0
            else "unchanged" if change == 0 else "no prior period"
        ),
    }


def build_overall_mentions(payload: dict, mentions: list[dict], previous: dict | None) -> dict:
    """2.0 — total mentions, change vs the previous period, outlet segmentation.

    The framework notes clients must be told which sites/apps are covered, so
    the covered sources travel with the number.
    """
    total = len(mentions)
    previous_total = None
    if previous:
        previous_total = (
            previous.get("sentiment_framework", {}).get("overall_mentions", {}).get("total")
        )
    difference_pct = (
        round(100 * (total - previous_total) / previous_total, 1)
        if previous_total else None
    )

    segments = {"local_media": 0, "international_media": 0, "social_media": 0}
    covered: dict[str, set] = {k: set() for k in segments}
    for mention in mentions:
        platform = mention.get("platform")
        domain = platform if mention.get("source_type") == "article" else None
        segment = classify_outlet(platform, domain)
        segments[segment] += 1
        if platform:
            covered[segment].add(platform)

    return {
        "total": total,
        "previous_total": previous_total,
        "difference_pct": difference_pct,
        "segmentation": [
            {"outlet_type": key, "label": key.replace("_", " ").title(),
             "count": value, "share": _pct(value, total)}
            for key, value in segments.items()
        ],
        # Clarity on coverage is a framework requirement, not a nicety.
        "sources_covered": {k: sorted(v)[:40] for k, v in covered.items()},
    }


def _scoring_gap(payload: dict, analysed: int) -> str | None:
    """A warning when the corpus was collected but barely scored.

    A report that reads 0.0% positive off nothing scored is not thin, it is
    wrong — and it looks identical to a genuinely neutral subject. Say which
    one it is.
    """
    total = int((payload.get("volume_trends") or {}).get("total_mentions") or 0)
    if not total:
        return None
    if analysed == 0:
        return (f"None of the {total} collected mentions were scored, so no sentiment "
                "reading is possible. The figures below are absent, not zero.")
    if analysed < total * 0.5:
        return (f"Only {analysed} of {total} collected mentions were scored, so this "
                "reading rests on a minority of the corpus.")
    return None


def build_sentiment_section(payload: dict, sentiments: dict | None = None) -> dict:
    """3.0 — positive / negative / neutral totals and the score (pie chart)."""
    positive, negative, neutral = sentiment_counts(payload, sentiments)
    total = positive + negative + neutral
    return {
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "total_analyzed": total,
        "shares": {
            "positive": _pct(positive, total),
            "negative": _pct(negative, total),
            "neutral": _pct(neutral, total),
        },
        "sentiment_score": sentiment_score(positive, negative, neutral) if total else None,
        "chart": "pie",  # the framework specifies a pie chart here
        # Why the numbers look the way they do, when they look wrong.
        "scoring_gap": _scoring_gap(payload, total),
    }


def build_current_issues(payload: dict) -> dict:
    """4.0 — potential levers (positive) and potential barriers (negative).

    The framework's own naming is kept: an analyst asked for levers and
    barriers, and renaming them would break the handover.
    """
    narratives = payload.get("narratives") or []

    def _issue(narrative: dict, kind: str) -> dict:
        return {
            "issue": narrative.get("label"),
            "description": narrative.get("description"),
            "mentions": narrative.get("mentions"),
            "strength": narrative.get("strength"),
            "growth": narrative.get("growth"),
            "type": kind,
        }

    positives, negatives = [], []
    for narrative in narratives:
        tone = str(narrative.get("tone") or narrative.get("sentiment") or "").lower()
        if tone == "positive":
            positives.append(_issue(narrative, "lever"))
        elif tone == "negative":
            negatives.append(_issue(narrative, "barrier"))

    # Without per-narrative tone, fall back to the analysts' own framing —
    # opportunities are levers and risks are barriers by definition.
    if not positives:
        positives = [
            {"issue": text[:120], "description": text, "type": "lever"}
            for text in (payload.get("opportunities") or [])[:MAX_CURRENT_ISSUES]
        ]
    if not negatives:
        negatives = [
            {"issue": text[:120], "description": text, "type": "barrier"}
            for text in (payload.get("risks") or [])[:MAX_CURRENT_ISSUES]
        ]

    return {
        "potential_levers": positives[:MAX_CURRENT_ISSUES],
        "potential_barriers": negatives[:MAX_CURRENT_ISSUES],
        "note": "Framework 4.0 caps current issues at three per side.",
    }


def build_emergent_issues(mentions: list[dict], now: datetime | None = None) -> dict:
    """5.0 — high-impact coverage in the 72 hours before the reporting date.

    The framework sets a concrete bar for social (engagement over ~100) and
    admits traditional media is harder to quantify; we therefore treat
    editorial coverage in the window as qualifying on its own and say so,
    rather than silently applying a social threshold to a newspaper.
    """
    reference = now or datetime.utcnow()
    cutoff = reference - timedelta(hours=EMERGENT_WINDOW_HOURS)

    emergent = []
    for mention in mentions:
        posted = mention.get("posted_at")
        if not isinstance(posted, datetime) or posted < cutoff:
            continue
        engagement = mention.get("engagement") or {}
        score = sum(int(engagement.get(k) or 0) for k in ("likes", "shares", "comments", "views"))
        segment = classify_outlet(mention.get("platform"))
        qualifies = (
            score >= HIGH_ENGAGEMENT_THRESHOLD
            if segment == "social_media"
            else True  # editorial coverage in-window is inherently notable
        )
        if not qualifies:
            continue
        emergent.append(
            {
                "headline": (mention.get("text") or "")[:200],
                "platform": mention.get("platform"),
                "outlet_type": segment,
                "engagement": score,
                "url": mention.get("source_url"),
                "posted_at": posted.isoformat(),
                "qualified_by": "engagement" if segment == "social_media" else "editorial coverage",
            }
        )

    return {
        "window_hours": EMERGENT_WINDOW_HOURS,
        "engagement_threshold": HIGH_ENGAGEMENT_THRESHOLD,
        "count": len(emergent),
        "items": rank_emergent(emergent, MAX_EMERGENT_ITEMS),
        "note": (
            f"Social items qualify above {HIGH_ENGAGEMENT_THRESHOLD} engagements; "
            "editorial coverage in the window qualifies on publication. Ranked "
            "within each outlet type and interleaved, so one platform's view "
            "counts cannot take every slot."
        ),
    }


def promo_ratio(text: str) -> float:
    """How much of an item is a pitch for its own channel.

    Returns the share of promo markers per 100 words, roughly. Used to demote,
    never to exclude: a genuine news video may still say "subscribe" once.
    """
    body = (text or "").lower()
    if not body.strip():
        return 0.0
    hits = sum(1 for marker in _PROMO_MARKERS if marker in body)
    words = max(len(body.split()), 1)
    return hits / (words / 100.0)


def rank_emergent(items: list[dict], limit: int) -> list[dict]:
    """Order emergent coverage so no single outlet type can take every slot.

    Sorting on raw engagement is the obvious thing and it is wrong here: a
    YouTube video carries a view count in the hundreds of thousands and a
    newspaper article carries no engagement figure at all, so editorial
    coverage loses every comparison and never appears. The numbers are not
    commensurable and pretending they are is what produced a clickbait feed.

    So: rank WITHIN each outlet type, where the comparison is meaningful, then
    interleave. Self-promoting items sink within their own segment rather than
    being suppressed outright — the judgement stays visible and reversible.
    """
    by_segment: dict[str, list[dict]] = {}
    for item in items:
        item["promo_ratio"] = round(promo_ratio(item.get("headline")), 2)
        by_segment.setdefault(item.get("outlet_type") or "other", []).append(item)

    for segment in by_segment.values():
        segment.sort(key=lambda i: (i["promo_ratio"] > 0, -i["engagement"]))

    # Round-robin, strongest of each segment first.
    ordered: list[dict] = []
    queues = [iter(v) for _, v in sorted(by_segment.items())]
    while queues and len(ordered) < limit:
        for queue in list(queues):
            item = next(queue, None)
            if item is None:
                queues.remove(queue)
                continue
            ordered.append(item)
            if len(ordered) >= limit:
                break
    return ordered


def build_strategic_implications(payload: dict, current_issues: dict) -> dict:
    """6.0 — the most important issue: outline, status, trajectory, dates, people."""
    narratives = payload.get("narratives") or []
    leading = max(
        narratives,
        key=lambda n: (n.get("strength") or 0, n.get("mentions") or 0),
        default=None,
    )

    barriers = current_issues.get("potential_barriers") or []
    if leading is None and barriers:
        leading = {"label": barriers[0].get("issue"), "description": barriers[0].get("description")}

    if leading is None:
        return {
            "issue": None,
            "outline": "No dominant issue identified in this period's coverage.",
            "status": None,
            "trajectory": None,
            "key_dates": [],
            "key_people": [],
        }

    growth = leading.get("growth")
    trajectory = (
        "escalating" if isinstance(growth, (int, float)) and growth > 0.2
        else "receding" if isinstance(growth, (int, float)) and growth < 0
        else "steady"
    )

    timeline = payload.get("timeline") or []
    key_dates = [
        {"date": item.get("date"), "event": item.get("event")}
        for item in timeline[:5]
        if item.get("date")
    ]
    key_people = [
        entry.get("who") for entry in (payload.get("influence") or [])[:5] if entry.get("who")
    ]

    return {
        "issue": leading.get("label"),
        "outline": leading.get("description") or "",
        "status": f"{leading.get('mentions') or 0} mention(s) in period; strength {leading.get('strength')}",
        "trajectory": trajectory,
        "key_dates": key_dates,
        "key_people": key_people,
    }


def build(politician, payload: dict, mentions: list[dict], previous: dict | None = None,
          now: datetime | None = None, sentiments: dict | None = None) -> dict:
    """Assemble the full framework payload in its documented order."""
    # Fill in who the subject is from the reference material in the corpus,
    # unless a caller supplied a profile of its own.
    if not payload.get("subject_profile"):
        profile = subject_profile_from_corpus(mentions)
        if profile:
            payload = {**payload, "subject_profile": profile}
    current_issues = build_current_issues(payload)
    return {
        "framework": "Sentiment Analysis Framework V1.0",
        "generated_at": (now or datetime.utcnow()).isoformat(),
        "summary_of_subject": build_summary_of_subject(politician, payload),
        "sentiment_score": build_sentiment_score_section(payload, previous, sentiments),
        "overall_mentions": build_overall_mentions(payload, mentions, previous),
        "sentiment": build_sentiment_section(payload, sentiments),
        "current_issues": current_issues,
        "emergent_issues": build_emergent_issues(mentions, now=now),
        "strategic_implications": build_strategic_implications(payload, current_issues),
    }
