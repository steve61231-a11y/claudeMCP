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
from collections import Counter
from datetime import datetime, timedelta

from engine.reports import storylines

# The framework's own threshold for "high impact" on social media.
HIGH_ENGAGEMENT_THRESHOLD = 100
EMERGENT_WINDOW_HOURS = 72
MAX_CURRENT_ISSUES = 3  # "we can stick to 3 maximum"
MAX_EMERGENT_ITEMS = 10

#: The client's instruction, in their words: "we kept at top three, but you
#: can also combine some... group two that are similar so that you still get
#: the top three, but you still get all the information." Three is therefore
#: a cap on THEMES, not on items — grouping is the mechanism by which three
#: slots carry a whole window.
MAX_EMERGENT_THEMES = 3

#: How many distinctive terms two items must share to be called the same
#: thing. Two, deliberately: in a corpus about one subject every item says
#: the subject's name, so a single shared word groups nothing.
GROUP_SHARED_TERMS = 2

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

# Outlet segmentation — local media / international media / social media —
# lives in `engine.reports.outlets`, built from what the sources actually
# return. The framework notes the exact list is agreed with each client, so
# the registries there are where a client's own list goes.


def classify_outlet(platform: str | None, domain: str | None = None) -> str:
    """local_media | international_media | social_media, from a platform and
    an outlet domain. A thin wrapper over `outlets.classify`, which decides on
    a whole corpus item — see that module for why the old domain check
    counted the Daily Nation as international coverage."""
    from engine.reports import outlets

    return outlets.classify({"platform": platform, "outlet_domain": domain,
                             "source_type": "article"})[0]


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
        "executive_summary": payload.get("summary") or "",
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
        # A bare "12.2%" is not a finding, it is a number. Say what it counts,
        # what it excludes, how confident it is, and what would move it.
        "reading": _score_reading(score, positive, negative, neutral, analysed,
                                  payload, previous_score),
        "scoring_gap": _scoring_gap(payload, analysed),
        "previous_score": previous_score,
        "change": change,
        "direction": (
            "improving" if change and change > 0 else "declining" if change and change < 0
            else "unchanged" if change == 0 else "no prior period"
        ),
    }


def _score_reading(score, positive: int, negative: int, neutral: int, analysed: int,
                   payload: dict, previous_score) -> list[str]:
    """Paragraphs explaining the headline number, in plain English.

    Every sentence here is arithmetic over counts already on the page. Nothing
    is inferred and nothing is asked of a model, because a fabricated
    interpretation of a real number is worse than no interpretation.
    """
    collected = int((payload.get("volume_trends") or {}).get("total_mentions") or 0)
    if not analysed:
        return [
            f"No sentiment reading is possible for this period. Of the {collected} "
            "mentions collected, none were scored, so the figures on this page are "
            "absent rather than zero.",
            "Treat every percentage in this section as missing data. A subject with "
            "genuinely no positive coverage and a run that scored nothing produce the "
            "same 0%, and they mean opposite things.",
        ]

    out = [
        f"The score is {score}%. It is the share of scored mentions that read as "
        f"positive: {positive} positive out of {analysed} scored "
        f"({neutral} neutral, {negative} negative). It is not a rating of the "
        "subject, an approval figure, or a prediction — it is the tone of what was "
        "published about them in this window.",
    ]

    if collected:
        pct = round(100 * analysed / collected)
        out.append(
            f"{analysed} of the {collected} mentions collected were scored ({pct}%). "
            + ("That is most of the corpus, so the reading is reasonably firm."
               if pct >= 50 else
               "That is a minority of the corpus, so treat the number as indicative "
               "rather than settled — the unscored remainder could move it in either "
               "direction.")
        )

    dominant = max((positive, "positive"), (neutral, "neutral"), (negative, "negative"))[1]
    if dominant == "neutral":
        out.append(
            f"The largest block is neutral ({neutral} of {analysed}, "
            f"{round(100 * neutral / analysed)}%). Neutral is the normal resting state of "
            "news coverage: reporting that states what happened without praising or "
            "attacking. A high neutral share usually means the subject is being covered "
            "rather than argued about, and it caps how high the positive share can go."
        )
    else:
        out.append(
            f"The largest block is {dominant}. Positive and negative together account for "
            f"{positive + negative} of {analysed} scored mentions "
            f"({round(100 * (positive + negative) / analysed)}%), which is the genuinely "
            "opinionated share of the coverage — the part that is arguing rather than "
            "reporting."
        )

    if negative:
        ratio = round(positive / negative, 2) if negative else None
        out.append(
            f"The positive-to-negative ratio is {ratio}:1 ({positive} against {negative}). "
            + ("Criticism outweighs praise in the scored coverage." if ratio and ratio < 1 else
               "Praise outweighs criticism in the scored coverage." if ratio and ratio > 1 else
               "Praise and criticism are evenly matched.")
            + " The narratives and per-source breakdowns below show which stories and "
              "which outlets that split comes from."
        )

    if previous_score is not None and score is not None:
        delta = round(score - previous_score, 1)
        out.append(
            f"Last period the score was {previous_score}%, so it has moved by {delta:+} "
            "points. A move of a few points on a corpus this size is noise; what matters "
            "is whether the narratives underneath it changed, which the Share of Voice "
            "section answers."
        )
    else:
        out.append(
            "There is no prior period to compare against, so the score has no trend yet. "
            "It becomes far more useful on the second run, when the movement between "
            "periods can be read rather than the level alone."
        )

    return out


def build_overall_mentions(payload: dict, mentions: list[dict], previous: dict | None,
                           sentiments: dict | None = None) -> dict:
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

    from engine.reports import outlets

    segments = {"local_media": 0, "international_media": 0, "social_media": 0}
    # Why each item landed where it did, counted — so a disputed split can
    # be audited ("312 Kenyan outlet, 40 GDELT places it in Kenya, 9 by
    # default") rather than argued about.
    bases: dict[str, int] = {}
    # A bare count of sources ("Sources covered (73)") is unusable: the reader
    # cannot tell which outlet said what, which way it leaned, or go and check
    # any of it. Build a per-source dossier instead — volume, tone split, and
    # the loudest items with their links.
    sources: dict[str, dict] = {}
    sentiments = sentiments or {}
    for mention in mentions:
        segment, basis = outlets.classify(mention)
        segments[segment] += 1
        bases[basis.split(" (")[0]] = bases.get(basis.split(" (")[0], 0) + 1
        source_name = outlets.publisher(mention)
        if not source_name:
            continue
        entry = sources.setdefault(source_name, {
            "source": source_name, "outlet_type": segment, "mentions": 0,
            "positive": 0, "neutral": 0, "negative": 0, "unscored": 0, "items": [],
        })
        entry["mentions"] += 1
        tone = storylines.sentiment_of(mention, sentiments)
        entry[tone or "unscored"] += 1
        entry["items"].append((_engagement_of(mention), mention, tone))

    sources_detail = []
    for entry in sources.values():
        top = sorted(entry.pop("items"), key=lambda t: t[0], reverse=True)[:5]
        entry["top_mentions"] = [
            {
                "author": m.get("author_handle"),
                "url": m.get("source_url"),
                "posted_at": m["posted_at"].isoformat()
                if hasattr(m.get("posted_at"), "isoformat") else m.get("posted_at"),
                "engagement": weight,
                "sentiment": tone,
                "platform": m.get("platform"),
                "excerpt": (m.get("text") or "").strip()[:400],
            }
            for weight, m, tone in top
        ]
        scored = entry["positive"] + entry["neutral"] + entry["negative"]
        # Lean is stated only where enough of that outlet was actually scored.
        # Calling an outlet negative off one scored item out of forty is a
        # fabricated editorial judgement about a real newsroom.
        if scored >= 3:
            if entry["positive"] > entry["negative"] * 1.5:
                entry["lean"] = "positive"
            elif entry["negative"] > entry["positive"] * 1.5:
                entry["lean"] = "negative"
            else:
                entry["lean"] = "mixed"
        else:
            entry["lean"] = None
        entry["scored"] = scored
        sources_detail.append(entry)
    sources_detail.sort(key=lambda e: e["mentions"], reverse=True)
    covered: dict[str, set] = {k: set() for k in segments}
    for entry in sources_detail:
        covered[entry["outlet_type"]].add(entry["source"])

    # Volume by day, dated items only — an undated item stamped on the last
    # day of the window once drew "peak 380 mentions in a day" over a corpus
    # of 699. With nothing datable there is no peak, not a peak of zero.
    by_day = Counter(posted.date().isoformat() for posted in
                     (storylines._posted(m) for m in mentions) if posted)
    peak = None
    if by_day:
        day, count = max(by_day.items(), key=lambda kv: (kv[1], kv[0]))
        peak = {"date": day, "count": count}

    return {
        "total": total,
        "previous_total": previous_total,
        "difference_pct": difference_pct,
        "by_day": dict(sorted(by_day.items())),
        "peak": peak,
        "dated": sum(by_day.values()),
        "segmentation": [
            {"outlet_type": key, "label": key.replace("_", " ").title(),
             "count": value, "share": _pct(value, total)}
            for key, value in segments.items()
        ],
        # Clarity on coverage is a framework requirement, not a nicety.
        "sources_covered": {k: sorted(v)[:40] for k, v in covered.items()},
        "segmentation_basis": dict(sorted(bases.items(), key=lambda kv: -kv[1])),
        "sources_detail": sources_detail,
    }


def _engagement_of(mention: dict) -> int:
    eng = mention.get("engagement") or {}
    return sum(int(eng.get(k, 0) or 0) for k in ("views", "likes", "shares", "comments"))


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


def sentiment_by_segment(mentions: list[dict], sentiments: dict | None) -> list[dict]:
    """The tone of Kenyan media, international media and social media,
    separately. One blended score hides the thing a client most needs to
    know: whether the press and the public are telling the same story."""
    from engine.reports import outlets

    tones = {seg: {"positive": 0, "negative": 0, "neutral": 0}
             for seg in (outlets.LOCAL, outlets.INTERNATIONAL, outlets.SOCIAL)}
    for mention in mentions or []:
        tone = storylines.sentiment_of(mention, sentiments)
        if tone:
            tones[outlets.classify(mention)[0]][tone] += 1
    rows = []
    for segment, tone in tones.items():
        scored = sum(tone.values())
        rows.append({"outlet_type": segment, "label": segment.replace("_", " ").title(),
                     **tone, "scored": scored,
                     "score": sentiment_score(tone["positive"], tone["negative"],
                                              tone["neutral"]) if scored else None,
                     "lean": storylines.lean(tone)})
    return rows


def sentiment_over_time(mentions: list[dict], sentiments: dict | None) -> dict | None:
    """Scored mentions by day — or by week across a long period — so a
    reader can see WHEN the tone turned, not only where it ended up. Dated
    items only: an undated item has no day to be counted on."""
    points = []
    for mention in mentions or []:
        tone = storylines.sentiment_of(mention, sentiments)
        when = storylines._posted(mention)
        if tone and when:
            points.append((when, tone))
    if not points:
        return None
    span = (max(p[0] for p in points) - min(p[0] for p in points)).days
    weekly = span > 35
    buckets: dict[str, dict] = {}
    for when, tone in points:
        day = when.date()
        key = (day - timedelta(days=day.weekday())).isoformat() if weekly else day.isoformat()
        bucket = buckets.setdefault(key, {"period": key, "positive": 0, "negative": 0,
                                          "neutral": 0})
        bucket[tone] += 1
    return {"granularity": "week" if weekly else "day",
            "series": [buckets[k] for k in sorted(buckets)]}


def sentiment_drivers(narratives: list[dict], profiles: dict, positive: int,
                      negative: int) -> dict:
    """Where the negative coverage sits, and where the positive does: each
    storyline's share of ALL negative (or positive) items. "Negative 41%"
    says how the period went; this says what it was about."""
    out: dict[str, list] = {"negative": [], "positive": []}
    for narrative in narratives:
        prof = _profile_of(profiles, narrative)
        if not prof:
            continue
        for side, whole in (("negative", negative), ("positive", positive)):
            count = prof["tone"][side]
            if count:
                out[side].append({
                    "issue": narrative.get("label"), "count": count,
                    "share_of_side": _pct(count, whole) if whole else None,
                    "mentions": prof["mentions"],
                    "reaction_thread": bool(storylines.is_chatter(narrative, prof)),
                })
    for side in out:
        out[side].sort(key=lambda d: d["count"], reverse=True)
        out[side] = out[side][:4]
    return out


def build_sentiment_section(payload: dict, sentiments: dict | None = None,
                            mentions: list[dict] | None = None,
                            profiles: dict | None = None) -> dict:
    """3.0 — positive / negative / neutral totals and the score (pie chart),
    then what is under them: the tone of each kind of outlet, how it moved
    through the period, and which storylines carried each side."""
    positive, negative, neutral = sentiment_counts(payload, sentiments)
    total = positive + negative + neutral
    extra = {}
    if mentions:
        extra["by_segment"] = sentiment_by_segment(mentions, sentiments)
        extra["over_time"] = sentiment_over_time(mentions, sentiments)
    if profiles:
        extra["drivers"] = sentiment_drivers(payload.get("narratives") or [], profiles,
                                             positive, negative)
    return {
        **extra,
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


def _evidence_for(label: str | None, payload: dict) -> dict:
    """The evidence behind one issue: who drives it, both framings, real quotes.

    Every one of these already exists in the payload. `narrative_deep_dives`
    carries how a storyline unfolded, who is pushing it and how supporters and
    critics each frame it; `public_voice` carries what ordinary people said, in
    their own words, with the mention each quote came from.

    None of it reached the framework. Section 4.0 rendered an issue as one
    generic sentence — "a growing negative narrative" — while the sentences
    that make it worth reading sat unused in the same payload. That is the
    difference between a report a client pays for and one they could have
    written themselves.
    """
    if not label:
        return {}
    wanted = label.strip().lower()

    dive = next(
        (d for d in (payload.get("narrative_deep_dives") or [])
         if isinstance(d, dict) and str(d.get("label", "")).strip().lower() == wanted),
        None,
    )

    # Citizen voice is not keyed by narrative, so take the themes whose wording
    # overlaps the issue — better an honest near-match than an empty section.
    voice_quotes: list[dict] = []
    voice = payload.get("public_voice") or {}
    terms = {w for w in wanted.split() if len(w) > 3}
    for stance in ("critical", "supportive", "neutral"):
        for theme in (voice.get(stance) or []):
            if not isinstance(theme, dict):
                continue
            haystack = f"{theme.get('theme','')} {theme.get('summary','')}".lower()
            if terms and not any(t in haystack for t in terms):
                continue
            for quote in (theme.get("quotes") or [])[:2]:
                voice_quotes.append({**quote, "stance": stance})

    evidence = {}
    if dive:
        evidence.update({
            "how_it_unfolded": dive.get("how_it_unfolded"),
            "driven_by": dive.get("who_is_driving_it") or [],
            "supporter_framing": dive.get("supporter_framing"),
            "critic_framing": dive.get("critic_framing"),
            "quotes": (dive.get("quotes") or [])[:4],
            "origin": dive.get("origin") or {},
        })
    if voice_quotes:
        evidence.setdefault("quotes", [])
        evidence["public_voice"] = voice_quotes[:4]
    return evidence


def _as_text_list(value) -> list[str]:
    """Whatever the model returned, as a list of strings.

    `risks` and `opportunities` are documented as lists of strings, and a model
    that answers with an object instead made `value[:3]` raise "unhashable
    type: slice" — which took the entire Sentiment Framework, the client
    deliverable, off the page. A section is worth having in a shape we did not
    ask for; it is not worth losing the whole tab over."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        # {"risks": [...]} nested one level deeper than the contract, or an
        # object keyed by theme — take the values either way.
        items: list = []
        for nested in value.values():
            items.extend(nested if isinstance(nested, list) else [nested])
        value = items
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            text = item.get("text") or item.get("risk") or item.get("opportunity") \
                or item.get("issue") or item.get("description")
            if isinstance(text, str) and text.strip():
                out.append(text.strip())
    return out


#: How many items must take a side before a storyline is called a lever or
#: a barrier on its own scored mentions.
MIN_SIDE = 2


def trajectory(narrative: dict) -> tuple[str | None, str | None]:
    """Which way a storyline moved across the period, and the counts that
    say so. None where nothing was measured: "escalating" printed over a
    storyline whose rate came from a pile of undated items stamped on the
    last day was a direction nobody observed."""
    growth = narrative.get("growth")
    halves = narrative.get("growth_halves") or {}
    first, second = halves.get("first"), halves.get("second")
    counted = (f"{second} dated mention(s) in the second half of the period against "
               f"{first} in the first") if first is not None and second is not None else None
    if isinstance(growth, (int, float)):
        label = ("escalating" if growth > 0.2 else "receding" if growth < -0.2 else "steady")
        return label, counted or f"{round(100 * growth):+d}% between the two halves of the period"
    if narrative.get("growth_basis") == "no_earlier_half" and (second or 0) >= 3:
        return "new", (f"all {second} dated mentions fall in the second half of the "
                       "period — it was not running before")
    if counted:
        return None, f"too few dated mentions to say which way it is moving ({counted})"
    return None, None


def _profile_of(profiles: dict | None, narrative: dict) -> dict | None:
    """A storyline's profile. Keyed by the storyline itself, not its label:
    two storylines can carry the same derived label, and a lookup by label
    would describe one with the other's items."""
    if not profiles:
        return None
    return profiles.get(id(narrative)) or profiles.get(narrative.get("label"))


def _public(prof: dict | None) -> dict:
    """A storyline profile without its working parts."""
    if not prof:
        return {}
    return {k: v for k, v in prof.items() if not k.startswith("_")}


def build_current_issues(payload: dict, profiles: dict | None = None) -> dict:
    """4.0 — potential levers (positive) and potential barriers (negative).

    The framework's own naming is kept: an analyst asked for levers and
    barriers, and renaming them would break the handover.

    Which side a storyline is on is read from its own scored mentions.
    Storylines arrived here with no tone at all, so on every live report
    this section skipped every storyline and printed the analysts' generic
    risks and opportunities instead — the "so short" a reader complained
    of, in the section that should carry the most.
    """
    narratives = payload.get("narratives") or []
    profiles = profiles or {}

    def _issue(narrative: dict, kind: str) -> dict:
        label = narrative.get("label")
        prof = _profile_of(profiles, narrative)
        issue = {
            "issue": label,
            "description": narrative.get("description"),
            "mentions": narrative.get("mentions"),
            "strength": narrative.get("strength"),
            "growth": narrative.get("growth"),
            "type": kind,
            "evidence": _evidence_for(label, payload),
        }
        if prof:
            issue["profile"] = _public(prof)
            issue["mentions"] = prof["mentions"]
        issue["trajectory"], issue["trajectory_basis"] = trajectory(narrative)
        return issue

    positives, negatives, set_aside = [], [], []
    for narrative in narratives:
        prof = _profile_of(profiles, narrative)
        why = storylines.is_chatter(narrative, prof)
        if why:
            set_aside.append({"label": narrative.get("label"),
                              "mentions": (prof or {}).get("mentions", narrative.get("mentions")),
                              "reason": why})
            continue
        tone = str(narrative.get("tone") or narrative.get("sentiment") or "").lower()
        if not tone and prof:
            pos, neg = prof["tone"]["positive"], prof["tone"]["negative"]
            if neg > pos and neg >= MIN_SIDE:
                tone = "negative"
            elif pos > neg and pos >= MIN_SIDE:
                tone = "positive"
        if tone == "positive":
            positives.append(_issue(narrative, "lever"))
        elif tone == "negative":
            negatives.append(_issue(narrative, "barrier"))

    def _side(issue: dict, side: str) -> tuple:
        prof = issue.get("profile") or {}
        return ((prof.get("tone") or {}).get(side, 0), issue.get("mentions") or 0)

    positives.sort(key=lambda i: _side(i, "positive"), reverse=True)
    negatives.sort(key=lambda i: _side(i, "negative"), reverse=True)

    # Without per-narrative tone, fall back to the analysts' own framing —
    # opportunities are levers and risks are barriers by definition.
    fallback = []
    if not positives:
        positives = [
            {"issue": text[:120], "description": text, "type": "lever"}
            for text in _as_text_list(payload.get("opportunities"))[:MAX_CURRENT_ISSUES]
        ]
        if positives:
            fallback.append("levers")
    if not negatives:
        negatives = [
            {"issue": text[:120], "description": text, "type": "barrier"}
            for text in _as_text_list(payload.get("risks"))[:MAX_CURRENT_ISSUES]
        ]
        if negatives:
            fallback.append("barriers")

    return {
        "potential_levers": positives[:MAX_CURRENT_ISSUES],
        "potential_barriers": negatives[:MAX_CURRENT_ISSUES],
        "note": "Framework 4.0 caps current issues at three per side.",
        "basis": ("Each storyline's side is read from its own scored mentions: a barrier "
                  "carries more negative than positive coverage, a lever the reverse. "
                  "Ranked by how many items take that side."),
        # Where a side came from the analysts' written risks or opportunities
        # instead, because no storyline took it. Said, so a reader knows
        # which entries are counted and which are judgement.
        "from_analysts": fallback,
        "set_aside": set_aside,
    }


def build_emergent_issues(mentions: list[dict], now: datetime | None = None,
                          narratives: list[dict] | None = None) -> dict:
    """5.0 — high-impact coverage in the 72 hours before the reporting date.

    The framework sets a concrete bar for social (engagement over ~100) and
    admits traditional media is harder to quantify; we therefore treat
    editorial coverage in the window as qualifying on its own and say so,
    rather than silently applying a social threshold to a newspaper.
    """
    reference = now or datetime.utcnow()
    cutoff = reference - timedelta(hours=EMERGENT_WINDOW_HOURS)

    from engine.reports import outlets

    emergent = []
    for mention in mentions:
        posted = mention.get("posted_at")
        if not isinstance(posted, datetime) or posted < cutoff:
            continue
        # An item nobody could date was stamped with the end of the window,
        # which is inside every "last 72 hours" by construction — the whole
        # undated pile qualified as emergent. And a reaction is a response
        # to coverage, not coverage: a comment with a hundred likes is not
        # an issue that emerged.
        if mention.get("date_estimated") or mention.get("source_type") == "comment":
            continue
        engagement = mention.get("engagement") or {}
        score = sum(int(engagement.get(k) or 0) for k in ("likes", "shares", "comments", "views"))
        # The item decides, not its platform: NTV Kenya's channel is a
        # newsroom's coverage, and the platform alone called it social.
        segment = outlets.classify(mention)[0]
        qualifies = (
            score >= HIGH_ENGAGEMENT_THRESHOLD
            if segment == "social_media"
            else True  # editorial coverage in-window is inherently notable
        )
        if not qualifies:
            continue
        emergent.append(
            {
                # Carried so a group can be formed from the narrative the
                # mention already belongs to, which is exact, rather than
                # from guessing at its words.
                "mention_id": str(mention.get("id")) if mention.get("id") is not None else None,
                "headline": (mention.get("text") or "")[:200],
                "platform": mention.get("platform"),
                "publisher": outlets.publisher(mention),
                "outlet_type": segment,
                "engagement": score,
                "url": mention.get("source_url"),
                "posted_at": posted.isoformat(),
                "qualified_by": "engagement" if segment == "social_media" else "editorial coverage",
            }
        )

    ranked = rank_emergent(emergent, MAX_EMERGENT_ITEMS)
    return {
        "window_hours": EMERGENT_WINDOW_HOURS,
        "engagement_threshold": HIGH_ENGAGEMENT_THRESHOLD,
        "count": len(emergent),
        "items": ranked,
        # The same window, grouped. The flat list stays because it is what
        # the count and the threshold describe; the grouping is what a
        # reader is actually given.
        **group_emergent(ranked, narratives),
        "note": (
            f"Social items qualify above {HIGH_ENGAGEMENT_THRESHOLD} engagements; "
            "editorial coverage in the window qualifies on publication. Ranked "
            "within each outlet type and interleaved, so one platform's view "
            "counts cannot take every slot."
        ),
    }


def _distinctive_terms(text: str) -> set[str]:
    """The words in an item that could tell it apart from another one.

    The stopword list is borrowed wholesale from the narrative labeller
    rather than re-listed here. A second copy is a second thing to keep
    current, and the place this codebase keeps paying for the same bug
    twice is precisely where it wrote the rule down twice.
    """
    from engine.intelligence.narratives import _STOPWORDS, _WORD_RE, stem

    return {
        stem(match.lower())
        for match in _WORD_RE.findall(text or "")
        if len(match) >= 4 and match.lower() not in _STOPWORDS
    }


def group_emergent(items: list[dict], narratives: list[dict] | None = None,
                   limit: int = MAX_EMERGENT_THEMES) -> dict:
    """Ten headlines become three things that are happening.

    The section was a feed: up to ten items, each with its outlet type and
    its engagement count, in rank order. Read quickly it said nothing,
    because three of those items were the same story told by three outlets
    and the reader had to notice that themselves. The client's instruction
    was to keep three slots and group similar items into them, so that
    three lines still carry the whole window.

    Grouping is done the exact way first and the approximate way second.

    **Exact**: an emergent item is a mention, and the mentions have already
    been clustered into narratives elsewhere in this report. If the item
    belongs to one, that narrative names the group — and the same name then
    appears in Share of Voice and the theme mix, so a reader moving between
    sections sees one vocabulary instead of three.

    **Approximate**: anything left over is grouped on shared distinctive
    terms and named from its own words, with `labelled_by` saying so. A
    derived name is a description of what the items are about, which beats
    a number, and marking it lets the page be honest that nothing authored
    it.

    What does not fit in `limit` themes is RETURNED, not dropped. Three
    themes and a stated count of what else was in the window is a summary;
    three themes and silence about the rest is a claim that the window held
    three things.
    """
    buckets: list[dict] = []
    by_label: dict[str, dict] = {}

    # Which narrative each mention belongs to. Built once; `mention_ids` are
    # stringified because an id arrives as an int from the database and as a
    # str from the corpus builders, and matching those two was a bug here
    # before.
    narrative_of: dict[str, str] = {}
    for narrative in narratives or []:
        label = narrative.get("label")
        if not label:
            continue
        for mention_id in narrative.get("mention_ids") or []:
            narrative_of.setdefault(str(mention_id), label)

    def _bucket(label: str, how: str) -> dict:
        existing = by_label.get(label)
        if existing is None:
            existing = {"label": label, "labelled_by": how, "items": [],
                        "terms": set(), "common": None}
            by_label[label] = existing
            buckets.append(existing)
        return existing

    # How many items in this window use each term. A term that appears in
    # most of them groups nothing — in a corpus about one subject that is
    # every item — while a term in two of twelve is a real link between
    # exactly those two.
    term_sets = {id(item): _distinctive_terms(item.get("headline")) for item in items}
    window_freq: dict[str, int] = {}
    for terms in term_sets.values():
        for term in terms:
            window_freq[term] = window_freq.get(term, 0) + 1
    rare_ceiling = max(1, len(items) // 3)

    # Items the narratives already name go straight to their group. The rest
    # start as a group of one and are merged below.
    loose: list[dict] = []
    for item in items:
        label = narrative_of.get(str(item.get("mention_id")))
        if label:
            _bucket(label, "narrative")["items"].append(item)
            continue
        terms = term_sets[id(item)]
        loose.append({"label": None, "labelled_by": "derived", "items": [item],
                      "terms": set(terms), "common": set(terms)})

    def _overlap(a: dict, b: dict) -> tuple[float, float]:
        """How strongly two groups are the same thing.

        Measured against what each group's items ALL share, not their union.
        Against the union, A joins B over one word, C joins A over a
        different word, D joins C, and five unrelated headlines end up in
        one group named after none of them — single-link chaining, which
        here produced a theme called "Facilities Members Advocacy" holding
        five items out of six.

        A shared term that is rare in this window counts fully; a term half
        the window uses counts half. One rare shared word is the story; one
        common shared word is a coincidence.
        """
        shared = a["common"] & b["common"]
        if not shared:
            return (0.0, 0.0)
        score = sum(1.0 if window_freq.get(term, 0) <= rare_ceiling else 0.5
                    for term in shared)
        union = len(a["common"] | b["common"]) or 1
        return (score, len(shared) / union)

    # Merged best-first rather than first-fit.
    #
    # First-fit depends on the order items arrive in, and the order here is
    # engagement: the loudest item absorbs whatever it happens to touch
    # first, and a genuinely tighter pair further down the list is then too
    # late to form. Measured on one window, "approval delays" and "waiting
    # on approvals" were split because the first of them had already been
    # taken by a louder headline they shared one weaker word with. With ten
    # items at most, comparing every pair costs nothing.
    while len(loose) > 1:
        best, pair = (0.0, 0.0), None
        for i in range(len(loose)):
            for j in range(i + 1, len(loose)):
                rank = _overlap(loose[i], loose[j])
                if rank > best:
                    best, pair = rank, (i, j)
        if pair is None or best[0] < 1.0:
            break
        i, j = pair
        a, b = loose[i], loose[j]
        a["items"].extend(b["items"])
        a["terms"] |= b["terms"]
        a["common"] &= b["common"]
        loose.pop(j)

    buckets.extend(loose)

    from engine.intelligence.narratives import derived_label

    for bucket in buckets:
        if bucket["label"] is None:
            bucket["label"] = derived_label(
                [i.get("headline") or "" for i in bucket["items"]])[0]
        bucket["engagement"] = sum(int(i.get("engagement") or 0)
                                   for i in bucket["items"])
        bucket["count"] = len(bucket["items"])
        bucket["platforms"] = sorted({i.get("platform") for i in bucket["items"]
                                      if i.get("platform")})
        # Why these are together, for the derived ones. A group a reader
        # cannot see the reason for is a group they have to take on trust.
        bucket["shared_terms"] = (
            sorted(bucket["common"] or ())[:4]
            if bucket["labelled_by"] == "derived" and len(bucket["items"]) > 1
            else [])
        bucket.pop("terms", None)
        bucket.pop("common", None)

    # Ranked on engagement, which is the client's own measure of what
    # mattered in the window, with the item count as the tie-break.
    buckets.sort(key=lambda b: (b["engagement"], b["count"]), reverse=True)
    top, rest = buckets[:limit], buckets[limit:]

    # Shares of the window's engagement — or, where nothing in it carries
    # any (news articles report no likes), of its items. Engagement alone
    # printed "0% of the reaction" beside every theme in an all-editorial
    # window, which is a measurement of nothing.
    engagement_total = sum(b["engagement"] for b in buckets)
    item_total = sum(b["count"] for b in buckets) or 1
    basis = "engagement" if engagement_total else "items"
    for bucket in top:
        bucket["share"] = (round(100 * bucket["engagement"] / engagement_total, 1)
                           if engagement_total else round(100 * bucket["count"] / item_total, 1))

    return {
        "themes": top,
        "other_themes": rest,
        "other_item_count": sum(b["count"] for b in rest),
        "window_engagement": engagement_total or 1,
        "window_items": item_total,
        "share_basis": basis,
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


def _issue_terms(label: str | None, description: str | None) -> set[str]:
    return _distinctive_terms(f"{label or ''} {label or ''} {description or ''}")


def _issue_dates(prof: dict, narrative: dict, payload: dict,
                 sentiments: dict | None) -> list[dict]:
    """The dates of THIS issue: when it was first seen, its busiest day,
    the latest item — each with what was published that day — and the
    analysts' dated briefings that are about it.

    These came from the report's general timeline, so the key dates under
    the most important issue were the report's first five dates, about
    whatever happened on them."""
    items = prof["_items"]
    dated = sorted((m for m in items if storylines._posted(m)), key=storylines._posted)
    by_day = prof.get("by_day") or {}
    out: list[dict] = []

    def _moment(kind: str, day: str, chosen: list[dict], event: str) -> dict:
        return {"date": day, "kind": kind, "event": event,
                "mentions_that_day": by_day.get(day),
                "items": [storylines.item_card(m, sentiments) for m in chosen]}

    def _headline(m: dict) -> str:
        card = storylines.item_card(m)
        return f"{card['publisher']}: {card['excerpt'][:160]}"

    if dated:
        first = dated[0]
        first_day = storylines._posted(first).date().isoformat()
        out.append(_moment("first", first_day, [first], f"First seen. {_headline(first)}"))
        peak = prof.get("peak")
        if peak and peak["date"] != first_day and peak["count"] > 1:
            on_day = [m for m in dated if storylines._posted(m).date().isoformat() == peak["date"]]
            top = storylines._representative(on_day, 2)
            out.append(_moment("peak", peak["date"], top,
                               f"Busiest day, {peak['count']} items. {_headline(top[0])}"))
        last = dated[-1]
        last_day = storylines._posted(last).date().isoformat()
        if last_day not in {o["date"] for o in out}:
            out.append(_moment("latest", last_day, [last], f"Most recent. {_headline(last)}"))

    # The analysts' dated briefings, where they are about this issue: two
    # distinctive words in common, the same bar 5.0 uses to group items.
    terms = _issue_terms(narrative.get("label"), narrative.get("description"))
    for entry in payload.get("timeline") or []:
        if not isinstance(entry, dict) or not entry.get("date") or entry.get("before_period"):
            continue
        shared = terms & _distinctive_terms(str(entry.get("event") or ""))
        if len(shared) >= GROUP_SHARED_TERMS:
            out.append({"date": entry.get("date"), "kind": "briefing",
                        "event": entry.get("event"),
                        "quotes": (entry.get("quotes") or [])[:2],
                        "mentions_that_day": entry.get("mentions_that_day")})
    out.sort(key=lambda d: str(d.get("date") or ""))
    return out[:7]


def _status(prof: dict) -> str:
    """The issue's standing in one sentence of counts."""
    tone, scored = prof["tone"], prof["scored"]
    parts = [f"{prof['mentions']} mention(s)"]
    if prof.get("share_pct") is not None:
        parts[0] += f" — {prof['share_pct']:g}% of the period's coverage"
    carried = []
    if prof["published"]:
        carried.append(f"{prof['published']} published item(s) from {prof['outlets']} "
                       f"outlet(s)")
    if prof["reactions"]:
        carried.append(f"{prof['reactions']} reaction(s)")
    if carried:
        parts.append(" and ".join(carried))
    if scored:
        parts.append(f"of {scored} scored, {_pct(tone['negative'], scored):g}% negative and "
                     f"{_pct(tone['positive'], scored):g}% positive")
    return "; ".join(parts) + "."


def build_strategic_implications(payload: dict, current_issues: dict,
                                 profiles: dict | None = None, subject=None,
                                 sentiments: dict | None = None) -> dict:
    """6.0 — the most important issue: outline, status, trajectory, dates, people.

    "Most important" was "largest": on a live report it chose a thread of
    emoji and laughter under a video. It is now the storyline with the most
    PUBLISHED coverage — a reaction counts a quarter of an item — among
    storylines that are issues at all, and the section says why it won and
    what came next, so the choice can be argued with.
    """
    narratives = payload.get("narratives") or []
    profiles = profiles or {}

    candidates, set_aside = [], []
    for narrative in narratives:
        prof = _profile_of(profiles, narrative)
        why = storylines.is_chatter(narrative, prof)
        if why:
            set_aside.append({"label": narrative.get("label"), "reason": why,
                              "mentions": (prof or {}).get("mentions", narrative.get("mentions"))})
            continue
        weight = prof["weight"] if prof else (narrative.get("mentions") or 0)
        candidates.append(((weight, narrative.get("mentions") or 0,
                            narrative.get("strength") or 0), narrative, prof))
    candidates.sort(key=lambda c: c[0], reverse=True)

    leading, prof = (candidates[0][1], candidates[0][2]) if candidates else (None, None)
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
            "key_amplifiers": [],
            "narrative_evidence": [],
            "set_aside": set_aside,
        }

    trajectory_label, trajectory_basis = trajectory(leading)

    # People named IN the coverage, not the accounts posting it. Merged so
    # one person is one row, without the subject, without a role of "null",
    # and — where the storyline's items are in hand — counted in THIS
    # issue's items rather than across the whole report.
    named = (payload.get("network_insights") or {}).get("top_people") or []
    people = storylines.merge_people(named, subject)
    if prof:
        key_people = storylines.people_in(prof["_items"], people)
        people_basis = (f"named in this storyline's own {prof['mentions']} items"
                        if key_people else None)
    else:
        key_people, people_basis = people[:8], "named across the whole report"
    key_people = [{"name": p["name"], "role": p["role"], "affiliation": p["affiliation"],
                   "co_mentions": p["co_mentions"], "named_in": p.get("named_in"),
                   "of": p.get("of")} for p in key_people]

    # Who carried THIS issue. The report-wide influence ranking put
    # "Facebook, Daily Nation" under every issue whatever it was about.
    if prof:
        key_amplifiers = [{"handle": pub["name"], "outlet_type": pub["outlet_type"],
                           "items": pub["count"]} for pub in prof["publishers"]]
        amplifiers_basis = "who published this storyline's items"
    else:
        key_amplifiers = [
            {"handle": entry.get("who"), "platform": entry.get("platform"),
             "reach": entry.get("reach") or entry.get("score")}
            for entry in (payload.get("influence") or [])[:5] if entry.get("who")
        ]
        amplifiers_basis = "the report's most influential accounts"

    if prof:
        key_dates = _issue_dates(prof, leading, payload, sentiments)
    else:
        # The dated timeline entries the analyst wrote are mini-briefings, not
        # headlines — carry the whole thing, because "what happened and why it
        # mattered" is the section's entire job.
        key_dates = [
            {"date": item.get("date"), "event": item.get("event"), "kind": "briefing",
             "quotes": (item.get("quotes") or [])[:2],
             "mentions_that_day": item.get("mentions_that_day")}
            for item in (payload.get("timeline") or [])[:5]
            if item.get("date")
        ]

    if prof:
        chosen_because = (
            f"The storyline with the most published coverage this period: "
            f"{prof['published']} published item(s) and {prof['reactions']} reaction(s)"
            + (f", ahead of “{candidates[1][1].get('label')}”" if len(candidates) > 1 else "")
            + ". Reactions count a quarter of a published item, so a busy comment "
              "thread cannot outrank the news.")
    else:
        chosen_because = "The largest storyline in the period."

    contenders = []
    for _, narrative, other in candidates[1:3]:
        contenders.append({"issue": narrative.get("label"),
                           "mentions": (other or {}).get("mentions", narrative.get("mentions")),
                           "published": (other or {}).get("published"),
                           "lean": (other or {}).get("lean")})

    return {
        "issue": leading.get("label"),
        "outline": leading.get("description") or "",
        "chosen_because": chosen_because,
        "status": _status(prof) if prof else (
            f"{leading.get('mentions') or 0} mention(s) in the period."),
        "status_detail": ({k: v for k, v in _public(prof).items() if k != "representative"}
                          if prof else None),
        "trajectory": trajectory_label,
        "trajectory_basis": trajectory_basis,
        "key_dates": key_dates,
        "key_people": key_people,
        "people_basis": people_basis,
        "key_amplifiers": key_amplifiers,
        "amplifiers_basis": amplifiers_basis,
        "representative": (prof or {}).get("representative") or [],
        "contenders": contenders,
        "set_aside": set_aside,
        # The posts behind the leading narrative, so 6.0 can be opened too.
        "narrative_evidence": leading.get("evidence") or [],
        # The same evidence 4.0 carries, for the issue that matters most.
        "evidence": _evidence_for(leading.get("label"), payload),
        # The report's single most important read — about the whole period,
        # not necessarily this issue, and labelled so on the page.
        "the_one_thing": ((payload.get("deep_insights") or {}).get("the_one_thing") or None),
    }


def normalise_payload(payload: dict) -> dict:
    """Accept either the pipeline payload or the frontend-shaped one.

    This module was written against the shape `_build_frontend_payload`
    produces — `narratives`, `influence`, `summary` — and the pipeline hands it
    the raw payload, which calls those `narrative_breakdown`,
    `influence_summary` and `executive_summary`. Every read of the three
    returned None.

    The cost was not subtle. 6.0 picks the leading narrative to decide the most
    important issue, so with narratives permanently invisible it answered "No
    dominant issue identified in this period's coverage" on every report ever
    produced, whatever the data said. 4.0 lost its narrative route entirely and
    fell back to risks/opportunities, and 1.0's executive summary was blank
    because neither key it looked for existed either.

    Normalising here rather than renaming keys elsewhere: both shapes are real
    and both callers are legitimate, so the adapter belongs at the boundary
    that consumes them.
    """
    out = dict(payload)

    narratives = payload.get("narratives") or payload.get("narrative_breakdown") or []
    out["narratives"] = [
        {
            "label": n.get("label"),
            "description": n.get("description"),
            # strength_score/mention_count/growth_rate are the pipeline's names
            # for the same three numbers.
            "strength": n.get("strength", n.get("strength_score")),
            "mentions": n.get("mentions", n.get("mention_count")),
            "growth": n.get("growth", n.get("growth_rate")),
            "tone": n.get("tone") or n.get("sentiment"),
            "mention_ids": n.get("mention_ids") or [],
            # Dropped here until now, so 6.0's "narrative_evidence" read a
            # key this adapter had already thrown away: the receipts were
            # empty on every report.
            "evidence": n.get("evidence") or [],
            "labelled_by": n.get("labelled_by"),
            "growth_basis": n.get("growth_basis"),
            "growth_halves": n.get("growth_halves"),
            "strength_basis": n.get("strength_basis"),
        }
        for n in narratives
        if isinstance(n, dict)
    ]

    influence = payload.get("influence") or payload.get("influence_summary") or []
    out["influence"] = [
        {
            "who": i.get("who") or i.get("author_handle"),
            "score": i.get("score"),
            "sentiment": i.get("sentiment", i.get("sentiment_contribution")),
        }
        for i in influence
        if isinstance(i, dict)
    ]

    out["summary"] = (
        payload.get("executiveBrief")
        or payload.get("executive_brief")
        or payload.get("summary")
        or payload.get("executive_summary")
        or ""
    )
    return out


def build(politician, payload: dict, mentions: list[dict], previous: dict | None = None,
          now: datetime | None = None, sentiments: dict | None = None) -> dict:
    """Assemble the full framework payload in its documented order."""
    payload = normalise_payload(payload)
    # Fill in who the subject is from the reference material in the corpus,
    # unless a caller supplied a profile of its own.
    if not payload.get("subject_profile"):
        profile = subject_profile_from_corpus(mentions)
        if profile:
            payload = {**payload, "subject_profile": profile}
    # What each storyline holds, counted once from the corpus and read by
    # 3.0, 4.0 and 6.0 alike — three sections describing one storyline from
    # three separate computations would disagree with each other.
    by_id = storylines.index_corpus(mentions)
    profiles = {}
    for narrative in payload.get("narratives") or []:
        prof = storylines.profile(narrative, by_id, sentiments, len(mentions))
        if prof:
            profiles[id(narrative)] = prof
    current_issues = build_current_issues(payload, profiles)
    return {
        "framework": "Sentiment Analysis Framework V1.0",
        "generated_at": (now or datetime.utcnow()).isoformat(),
        "summary_of_subject": build_summary_of_subject(politician, payload),
        "sentiment_score": build_sentiment_score_section(payload, previous, sentiments),
        "overall_mentions": build_overall_mentions(payload, mentions, previous, sentiments),
        "sentiment": build_sentiment_section(payload, sentiments, mentions, profiles),
        "current_issues": current_issues,
        "emergent_issues": build_emergent_issues(
            mentions, now=now, narratives=payload.get("narratives")),
        "strategic_implications": build_strategic_implications(
            payload, current_issues, profiles, politician, sentiments),
    }
