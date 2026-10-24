"""What the public went looking for — search demand, the third public signal.

The report read two signals of public attention: what was PUBLISHED (the
press) and what was SAID (social media). Both are performances — written for
an audience, and both can be bought. Search is what people want to KNOW,
typed where nobody is watching. Read beside the other two, it answers what
neither can alone:

- who led — did searching rise before the coverage (something moving outside
  the media), after it (the press drove the curiosity), or not at all
  (coverage nobody went looking for, which a client need not answer);
- what the public is asking that nothing collected addresses — an information
  gap the client can fill, or a gap in collection the next run should close;
- which storylines the public is actually following.

The data is Google Trends for Kenya, through SocialCrawl (already this
product's paid social backbone): interest over time for the subject's name,
and the top and rising searches around it. Three rules, all the founding
rule:

1. **Three states, never two.** `measured`; `too_little_search` — Google
   publishes nothing for a term searched too rarely, which is itself a finding
   about public attention; and `not_measured`, with the reason (no key, no
   credits, a refusal, a timeout). A failed request must never read as
   "nobody searched".
2. **An index is not a count.** Trends values run 0-100, where 100 is the
   busiest moment IN THE WINDOW ASKED, for the place asked. They are never
   summed across requests, never called searches, and every sentence that
   carries one says what 100 is.
3. **A search is not a fact.** People searching "X arrested" is evidence of
   attention, not of an arrest. The writers may cite search data only in a
   sentence that says it is about searching (`from_facts.check_sentence`).

Nothing here calls a model.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from statistics import median
from urllib.parse import quote

#: Google Trends presets, smallest first. Each ends NOW, which is why a
#: report about a period that ended days ago cannot be served by one.
TIMEFRAMES = (
    (1.0, "past_day", "the past 24 hours"),
    (7.0, "past_7_days", "the past 7 days"),
    (30.0, "past_30_days", "the past 30 days"),
    (90.0, "past_90_days", "the past 90 days"),
    (365.0, "past_12_months", "the past 12 months"),
    (1826.0, "past_5_years", "the past 5 years"),
)
#: Trends' own URL spelling of each preset, for the link a reader can open.
TRENDS_DATE = {"past_day": "now 1-d", "past_7_days": "now 7-d", "past_30_days": "today 1-m",
               "past_90_days": "today 3-m", "past_12_months": "today 12-m", "past_5_years": "today 5-y"}
#: How long after a period's end its search can still be read from a preset
#: that ends today without misdescribing the window.
STALE_AFTER = timedelta(days=2)

#: Google labels a rise of more than 5,000% "Breakout" rather than a number.
BREAKOUT = 5000

#: Words that make a search a way of FINDING the subject rather than a
#: question ABOUT them. "martha karua news" asks nothing.
NAVIGATIONAL = {
    "news", "latest", "today", "now", "live", "video", "videos", "youtube", "tiktok",
    "twitter", "facebook", "instagram", "photos", "photo", "pictures", "images", "image",
    "wikipedia", "wiki", "biography", "bio", "profile", "kenya", "kenyan", "nairobi",
    "update", "updates", "breaking", "speech", "interview", "the", "and", "for", "with",
}
_STOP = {"what", "when", "where", "which", "who", "whom", "whose", "why", "how", "does", "did",
         "is", "are", "was", "were", "has", "have", "had", "about", "from", "this", "that",
         "will", "would", "can", "could", "should", "his", "her", "their", "its"}

SOCIALCRAWL_PATHS = {"explore": "/v1/google_trends/explore", "rising": "/v1/google_trends/rising"}


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def timeframe_for(window_start: datetime, window_end: datetime,
                  now: datetime | None = None) -> tuple[str | None, str | None, str | None]:
    """(preset, label, reason-it-cannot-be-read). The smallest preset that
    reaches back to the period's start — presets end now, so the period must
    end close to now for one to describe it."""
    now = now or datetime.utcnow()
    if now - window_end > STALE_AFTER:
        return None, None, (f"this period ended on {window_end.date().isoformat()}; Google Trends "
                            "reads windows that end today, so this period's search interest cannot "
                            "be read on its own")
    days = (now - window_start).total_seconds() / 86400
    for limit, preset, label in TIMEFRAMES:
        if days <= limit + 0.5:
            return preset, label, None
    return TIMEFRAMES[-1][1], TIMEFRAMES[-1][2], None


def trends_url(keyword: str, preset: str, location: str) -> str:
    """The Google Trends page for exactly what was asked, so a reader can
    open it and see the same curve."""
    return (f"https://trends.google.com/trends/explore?date={quote(TRENDS_DATE.get(preset, 'today 1-m'))}"
            f"&geo={quote(location)}&q={quote(keyword)}")


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

def _classify(status: int | None, body: str, error: str | None) -> tuple[str, str | None]:
    """A response's state: `measured`, `too_little_search` or `not_measured`
    with a reason in words an operator can act on."""
    if error:
        return "not_measured", error
    if status == 200:
        return "measured", None
    if status == 404:
        # The contract: "a 404 means the keyword had no data there".
        return "too_little_search", None
    if status == 402 or "INSUFFICIENT_CREDITS" in (body or ""):
        return "not_measured", ("the SocialCrawl account behind SOCIALCRAWL_API_KEY has no credits "
                                "left; top it up to read search demand. Nothing else in the report "
                                "depends on it.")
    if status == 401:
        return "not_measured", "SocialCrawl refused SOCIALCRAWL_API_KEY (HTTP 401)"
    return "not_measured", f"SocialCrawl answered HTTP {status}: {(body or '')[:160]}"


def ask(api_key: str, kind: str, params: dict) -> dict:
    """One SocialCrawl Trends request → {"state", "reason", "data"}."""
    from engine.ingestion import http
    from engine.ingestion.socialcrawl_connector import SOCIALCRAWL_BASE_URL

    if not api_key:
        return {"state": "not_measured", "reason": "SOCIALCRAWL_API_KEY is not set", "data": None}
    try:
        response = http.get(f"{SOCIALCRAWL_BASE_URL}{SOCIALCRAWL_PATHS[kind]}", params=params,
                            headers={"x-api-key": api_key}, timeout=35)
    except Exception as exc:  # noqa: BLE001 — a timeout is not an answer
        state, reason = _classify(None, "", f"{type(exc).__name__}: {exc}"[:200])
        return {"state": state, "reason": reason, "data": None}
    text = response.text if hasattr(response, "text") else ""
    state, reason = _classify(response.status_code, text, None)
    data = None
    if state == "measured":
        try:
            body = response.json()
        except ValueError:
            return {"state": "not_measured", "reason": "SocialCrawl's answer was not JSON", "data": None}
        data = body.get("data", body) if isinstance(body, dict) else None
    return {"state": state, "reason": reason, "data": data}


def keywords_for(name: str, aliases: list[str] | None) -> list[str]:
    """The subject's full name, and the one alias most likely to be what
    people type — often the surname alone ("Karua", "Sifuna"), which is a
    different search term from the full name and in Kenya frequently the
    more searched. Both are asked in one request, normalised together, and
    the more searched is the one expanded; the page names which."""
    out = [name.strip()]
    for alias in sorted(aliases or [], key=len, reverse=True):
        alias = str(alias or "").strip()
        if len(alias) >= 4 and alias.lower() != name.lower():
            out.append(alias)
            break
    return [k for k in out if 2 <= len(k) <= 100]


def fetch(name: str, aliases: list[str] | None, window_start: datetime, window_end: datetime,
          api_key: str, location: str = "KE", now: datetime | None = None,
          asker=ask, keywords: list[str] | None = None) -> dict:
    """Ask Trends what it knows: interest over time for the name (and one
    alias, normalised together so they compare), then the top and rising
    searches around whichever was searched more. Two requests; touches no
    database, so it runs beside the analysts."""
    preset, label, why_not = timeframe_for(window_start, window_end, now)
    base = {"location": location, "timeframe": preset, "timeframe_label": label,
            "asked_at": (now or datetime.utcnow()).isoformat(timespec="seconds")}
    if why_not:
        return {**base, "explore": {"state": "not_measured", "reason": why_not, "data": None},
                "rising": {"state": "not_measured", "reason": why_not, "data": None}, "keywords": []}
    # The Issue Map names its own pair (principal and issue, normalised
    # together); a report asks the name and its likeliest alias.
    keywords = [k for k in (keywords or keywords_for(name, aliases)) if 2 <= len(k) <= 100][:5]
    where = {"location": location} if location else {}
    explore = asker(api_key, "explore", {"keywords": ",".join(keywords), "timeframe": preset, **where})
    chosen = keywords[0]
    if explore["state"] == "measured":
        averages = {str(a.get("keyword") or "").lower(): float(a.get("value") or 0)
                    for a in (explore["data"] or {}).get("averages") or [] if isinstance(a, dict)}
        best = max(keywords, key=lambda k: averages.get(k.lower(), 0.0))
        if averages.get(best.lower(), 0.0) > 0:
            chosen = best
    if explore["state"] == "not_measured" and "credits" in (explore["reason"] or ""):
        # The second request would fail the same way and cost a round trip.
        rising = {"state": "not_measured", "reason": explore["reason"], "data": None}
    else:
        rising = asker(api_key, "rising", {"keyword": chosen, "timeframe": preset, **where})
    return {**base, "keywords": keywords, "keyword": chosen, "explore": explore, "rising": rising}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _points(explore_data: dict | None, keyword: str) -> list[dict]:
    """The chosen keyword's points, whatever shape a series takes."""
    for series in (explore_data or {}).get("series") or []:
        if not isinstance(series, dict):
            continue
        if str(series.get("keyword") or "").lower() != keyword.lower() and len(
                (explore_data or {}).get("series") or []) > 1:
            continue
        out = []
        for p in series.get("points") or []:
            if not isinstance(p, dict) or p.get("value") is None:
                continue
            stamp = str(p.get("datetime") or p.get("date") or "")
            try:
                value = float(p["value"])
            except (TypeError, ValueError):
                continue
            out.append({"at": stamp, "date": stamp[:10], "value": value, "partial": bool(p.get("partial"))})
        return out
    return []


def _daily(points: list[dict]) -> dict[str, float]:
    """One value per day — the day's highest, because an hourly series'
    question is "did people go looking that day", not its average hour."""
    days: dict[str, float] = {}
    for p in points:
        if len(p["date"]) == 10:
            days[p["date"]] = max(days.get(p["date"], 0.0), p["value"])
    return days


def _terms(text: str, drop: set[str]) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _STOP and w not in drop}


def _stem(word: str) -> str:
    for suffix in ("ings", "ing", "ers", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _queries(rising_data: dict | None) -> tuple[list[dict], list[dict]]:
    """(top, rising), each [{"query", "value"|"growth", "breakout"}]. Google
    writes a breakout as text; it is kept as a flag, never turned into a
    number nobody measured."""
    top, rising = [], []
    for row in (rising_data or {}).get("top") or []:
        if isinstance(row, dict) and str(row.get("query") or "").strip():
            value = row.get("value")
            top.append({"query": str(row["query"]).strip(),
                        "value": float(value) if isinstance(value, (int, float)) else None})
    for row in (rising_data or {}).get("rising") or []:
        if not isinstance(row, dict) or not str(row.get("query") or "").strip():
            continue
        raw = row.get("value", row.get("growth", row.get("increase_percent")))
        formatted = str(row.get("formatted_value") or row.get("formatted") or raw or "")
        breakout = "breakout" in formatted.lower() or (isinstance(raw, (int, float)) and raw >= BREAKOUT)
        growth = float(raw) if isinstance(raw, (int, float)) and not breakout else None
        rising.append({"query": str(row["query"]).strip(), "growth": growth, "breakout": breakout})
    return top, rising


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if not vx or not vy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (vx * vy) ** 0.5


#: Days compared before a lead or lag is stated at all.
MIN_DAYS = 10
#: The fit a stated lead or lag needs; below it the honest reading is that
#: the two did not move together this period.
MIN_FIT = 0.5


def lead_lag(search: dict[str, float], coverage: dict[str, int], days: list[str]) -> dict:
    """Did search lead coverage, follow it, move with it, or neither?

    The search and coverage series are compared at offsets of up to three
    days; the offset where they fit best decides. A fit below MIN_FIT, or
    fewer than MIN_DAYS days, is reported as such — a lead read off noise is
    a claim nobody measured."""
    if len(days) < MIN_DAYS:
        return {"reading": "too_few_days", "days": len(days)}
    s = [search.get(d, 0.0) for d in days]
    c = [float(coverage.get(d, 0)) for d in days]
    fits = {}
    for lag in range(-3, 4):
        # lag > 0: coverage on day t+lag is compared with search on day t,
        # so a best fit at lag > 0 means searching came first.
        if lag >= 0:
            xs, ys = s[: len(s) - lag], c[lag:]
        else:
            xs, ys = s[-lag:], c[: len(c) + lag]
        r = _pearson(xs, ys)
        if r is not None:
            fits[lag] = r
    if not fits:
        return {"reading": "flat", "days": len(days)}
    best = max(fits, key=lambda k: (fits[k], -abs(k)))
    r = fits[best]
    if r < MIN_FIT:
        reading = "apart"
    elif best > 0:
        reading = "search_led"
    elif best < 0:
        reading = "coverage_led"
    else:
        reading = "together"
    return {"reading": reading, "lag_days": best, "fit": round(r, 2), "days": len(days)}


def _coverage_peaks(coverage: dict[str, int], days: list[str], most: int = 3) -> list[str]:
    counts = [coverage.get(d, 0) for d in days]
    if not counts:
        return []
    typical = median(counts)
    peaks = [d for d in days if coverage.get(d, 0) >= max(3, 2 * typical)]
    return sorted(sorted(peaks, key=lambda d: coverage.get(d, 0), reverse=True)[:most])


def traction(search: dict[str, float], coverage: dict[str, int], days: list[str]) -> dict:
    """For each of the busiest days of coverage: did searching rise within a
    day before or two days after? And the days the public searched while the
    press was quiet. Counted against the period's typical day, not a bar
    anyone chose."""
    if not days or not search:
        return {"coverage_peaks": [], "search_without_coverage": []}
    s_typical = median([search.get(d, 0.0) for d in days])
    c_typical = median([coverage.get(d, 0) for d in days])
    index = {d: i for i, d in enumerate(days)}
    peaks = []
    for day in _coverage_peaks(coverage, days):
        i = index[day]
        near = [search.get(days[j], 0.0) for j in range(max(0, i - 1), min(len(days), i + 3))]
        high = max(near) if near else 0.0
        peaks.append({"date": day, "items": coverage.get(day, 0), "search_high": high,
                      "search_typical": s_typical,
                      "moved": high >= max(2 * s_typical, s_typical + 25)})
    quiet = []
    for day in sorted(days, key=lambda d: search.get(d, 0.0), reverse=True)[:3]:
        i = index[day]
        around = [coverage.get(days[j], 0) for j in range(max(0, i - 1), min(len(days), i + 2))]
        if search.get(day, 0.0) >= max(60.0, 2 * s_typical) and max(around or [0]) <= c_typical:
            quiet.append({"date": day, "search": search.get(day, 0.0), "items": coverage.get(day, 0),
                          "coverage_typical": c_typical})
    return {"coverage_peaks": peaks, "search_without_coverage": sorted(quiet, key=lambda q: q["date"])}


def answered(query: str, subject_words: set[str], findings: list[dict], storylines: list[dict],
             corpus_texts: list[str]) -> dict:
    """Whether anything this report holds speaks to a search.

    `navigational` — the search finds the subject and asks nothing ("martha
    karua news"); `on_record` — a finding says something about it;
    `in_coverage` — collected items mention it, the record does not;
    `unanswered` — nothing collected does. The last is either a gap in the
    coverage a client can fill or a gap in collection the next run should
    close; it is never stated as "nobody has written about it"."""
    terms = {_stem(t) for t in _terms(query, subject_words | NAVIGATIONAL)}
    if not terms:
        return {"state": "navigational"}

    def hits(text: str) -> bool:
        return bool(terms & {_stem(w) for w in _terms(text, set())})

    for f in findings or []:
        if hits(f.get("statement") or ""):
            return {"state": "on_record", "finding": f.get("statement")}
    for story in storylines or []:
        if hits(f"{story.get('label', '')} {story.get('description', '')}"):
            return {"state": "on_record", "storyline": story.get("label")}
    if any(hits(t) for t in corpus_texts or []):
        return {"state": "in_coverage"}
    return {"state": "unanswered"}


def read(raw: dict, window_start: datetime, window_end: datetime, subject: str,
         coverage_by_day: dict | None = None, findings: list[dict] | None = None,
         storylines: list[dict] | None = None, corpus_texts: list[str] | None = None,
         previous: dict | None = None) -> dict:
    """The section, from what Trends returned and what the report holds."""
    raw = raw or {}
    location, preset, label = raw.get("location", "KE"), raw.get("timeframe"), raw.get("timeframe_label")
    keyword = raw.get("keyword") or (raw.get("keywords") or [subject])[0]
    explore, rising = raw.get("explore") or {}, raw.get("rising") or {}
    out = {"location": location, "timeframe": preset, "timeframe_label": label, "keyword": keyword,
           "keywords": raw.get("keywords") or [], "asked_at": raw.get("asked_at"),
           "trends_url": trends_url(keyword, preset, location) if preset else None,
           "source": "Google Trends via SocialCrawl",
           "place": location_name(location),
           "scale_note": (f"Google's 0-100 index for searches {in_place(location)}: 100 is the "
                          f"busiest moment of {label or 'the window asked'} there, not a number of searches.")}
    states = {explore.get("state"), rising.get("state")}
    if "measured" not in states:
        if "too_little_search" in states and "not_measured" not in states:
            out.update(state="too_little_search",
                       reason=(f"Too few people {in_place(location)} searched for "
                               f"“{keyword}” over {label} for Google to publish a figure."))
        else:
            out.update(state="not_measured",
                       reason=explore.get("reason") or rising.get("reason") or "no answer was received")
        fallbacks = [a.get("fallback_from") for a in (explore, rising) if a.get("fallback_from")]
        if fallbacks:
            out["fallback_from"] = fallbacks[0]
        return out
    out["state"] = "measured"
    out["parts"] = {"interest": explore.get("state"), "searches": rising.get("state")}
    # Which rung of the ladder answered each part — the page names it, and an
    # operator can see when the paid route failed and a free one stood in.
    out["providers"] = {k: v for k, v in (("interest", explore.get("provider")),
                                          ("searches", rising.get("provider"))) if v}
    fallbacks = [a.get("fallback_from") for a in (explore, rising) if a.get("fallback_from")]
    if fallbacks:
        out["fallback_from"] = fallbacks[0]
    for part, answer in (("interest", explore), ("searches", rising)):
        if answer.get("state") != "measured":
            out.setdefault("part_reasons", {})[part] = answer.get("reason") or (
                "too few searches to publish" if answer.get("state") == "too_little_search" else None)

    points = _points(explore.get("data"), keyword)
    start, end = window_start.date().isoformat(), window_end.date().isoformat()
    in_window = [p for p in points if start <= p["date"] <= end]
    out["series"] = in_window
    if explore.get("state") == "measured" and not points:
        # A 200 whose shape this parser did not recognise must not read as a
        # flat line: say what arrived.
        out.setdefault("part_reasons", {})["interest"] = (
            "the answer held no points this parser recognised (keys: "
            + ", ".join(sorted((explore.get("data") or {}).keys()))[:120] + ")")
    if in_window:
        peak = max(in_window, key=lambda p: p["value"])
        out["peak"] = {"at": peak["at"], "date": peak["date"], "value": peak["value"]}
        out["average"] = round(sum(p["value"] for p in in_window) / len(in_window), 1)
    averages = [{"keyword": a.get("keyword"), "average": a.get("value")}
                for a in (explore.get("data") or {}).get("averages") or [] if isinstance(a, dict)]
    if len(averages) > 1:
        out["comparison"] = averages

    # Search against coverage, day by day, on the days both cover.
    daily = _daily(in_window)
    coverage = {str(k)[:10]: int(v or 0) for k, v in (coverage_by_day or {}).items()}
    days = sorted(d for d in daily if start <= d <= end)
    if daily and coverage:
        out["lead_lag"] = lead_lag(daily, coverage, days)
        out["traction"] = traction(daily, coverage, days)
        # The coverage it was compared with, day by day, so the page draws
        # exactly the two series the reading rests on.
        out["coverage_by_day"] = {d: coverage.get(d, 0) for d in days}

    top, rising_q = _queries(rising.get("data"))
    subject_words = _terms(f"{subject} {keyword}", set())
    for q in top + rising_q:
        q["answer"] = answered(q["query"], subject_words, findings or [], storylines or [], corpus_texts or [])
    out["top_queries"], out["rising_queries"] = top[:15], rising_q[:15]
    out["unanswered"] = [q["query"] for q in rising_q + top if q["answer"]["state"] == "unanswered"][:8]

    # Which storylines the public was searching about.
    matches: dict[str, list[dict]] = {}
    for story in storylines or []:
        st = {_stem(t) for t in _terms(f"{story.get('label', '')} {story.get('description', '')}",
                                       subject_words | NAVIGATIONAL)}
        for kind, rows in (("rising", rising_q), ("top", top)):
            for q in rows:
                if st & {_stem(t) for t in _terms(q["query"], subject_words | NAVIGATIONAL)}:
                    matches.setdefault(story.get("label"), []).append({"query": q["query"], "kind": kind,
                                                                       "growth": q.get("growth"),
                                                                       "breakout": q.get("breakout")})
    if matches:
        out["storyline_searches"] = {label_: rows[:3] for label_, rows in matches.items()}

    # Since the last report: searches that were not in its lists.
    if previous and previous.get("state") == "measured":
        before = {str(q.get("query") or "").lower() for q in
                  (previous.get("top_queries") or []) + (previous.get("rising_queries") or [])}
        new = [q["query"] for q in rising_q + top if q["query"].lower() not in before]
        out["new_since_last"] = new[:8]
    return out


#: The places every report reads: Kenya in depth, and the world, because an
#: international angle — the IMF, the ICC, the diaspora, a foreign court —
#: often shows abroad before it shows at home. "" is Google's worldwide.
LOCATIONS = ("KE", "")


def location_name(code: str) -> str:
    return {"KE": "Kenya", "": "the world"}.get((code or "").upper(), code)


def in_place(code: str) -> str:
    """"in Kenya", "worldwide" — the phrase a sentence needs."""
    return "worldwide" if not code else f"in {location_name(code)}"


# ---------------------------------------------------------------------------
# For the writers — counted sentences, as ledger entries
# ---------------------------------------------------------------------------

def _pct(value: float) -> str:
    return f"{int(round(value)):,}%"


def signals(section: dict, subject: str) -> list[dict]:
    """Search demand as counted sentences, each with the numbers in it and
    the Trends page it can be checked against. They enter the writers'
    ledger as SEARCH entries: what people typed, never what happened."""
    if (section or {}).get("state") != "measured":
        return []
    place = in_place(section.get("location", "KE"))
    label = section.get("timeframe_label") or "the period"
    keyword = section.get("keyword") or subject
    out = []

    def add(sentence: str, numbers: dict, query: str | None = None):
        out.append({"kind": "search", "sentence": sentence, "numbers": numbers,
                    "url": trends_url(query or keyword, section.get("timeframe"), section.get("location", "KE"))})

    for q in (section.get("rising_queries") or [])[:3]:
        if q.get("breakout"):
            add(f"Searches {place} for “{q['query']}” were a breakout over {label}: up more than "
                f"{_pct(BREAKOUT)}, by Google's measure.", {"growth": BREAKOUT}, q["query"])
        elif q.get("growth"):
            add(f"Searches {place} for “{q['query']}” rose {_pct(q['growth'])} over {label}.",
                {"growth": q["growth"]}, q["query"])
    tr = section.get("traction") or {}
    peaks = tr.get("coverage_peaks") or []
    if peaks:
        moved = sum(1 for p in peaks if p.get("moved"))
        add(f"{moved} of the {len(peaks)} busiest days of coverage of {subject} were followed by a rise in "
            f"searches for “{keyword}” {place}.", {"moved": moved, "peaks": len(peaks)})
    for q in tr.get("search_without_coverage") or []:
        add(f"On {q['date']}, searches for “{keyword}” {place} reached {int(q['search'])} on Google's "
            f"0-100 scale while coverage stayed at {q['items']} item(s).",
            {"search": int(q["search"]), "items": q["items"]})
    ll = section.get("lead_lag") or {}
    if ll.get("reading") in ("search_led", "coverage_led", "together"):
        lag = abs(int(ll.get("lag_days") or 0))
        sentence = {
            "search_led": f"Over the {ll['days']} days compared, searches for “{keyword}” {place} tended "
                          f"to rise {lag} day(s) before coverage of {subject} did.",
            "coverage_led": f"Over the {ll['days']} days compared, searches for “{keyword}” {place} tended "
                            f"to rise {lag} day(s) after coverage of {subject} did.",
            "together": f"Over the {ll['days']} days compared, searches for “{keyword}” {place} rose and "
                        f"fell on the same days as coverage of {subject}.",
        }[ll["reading"]]
        add(sentence, {"days": ll["days"], "lag": lag})
    for query in (section.get("unanswered") or [])[:2]:
        add(f"People {place} searched for “{query}” over {label}, and nothing this report collected "
            f"addresses it.", {}, query)
    return out


# ---------------------------------------------------------------------------
# Both places, and what separates them
# ---------------------------------------------------------------------------

def gather(name: str, aliases: list[str] | None, window_start: datetime, window_end: datetime,
           api_key: str, now: datetime | None = None, asker=None,
           locations: tuple[str, ...] = LOCATIONS, suggest=None, readers=None,
           keywords: list[str] | None = None) -> dict:
    """Everything asked at once: Trends for each place (through the ladder —
    SocialCrawl, then Google directly), Google's suggestions for each place,
    and Wikipedia's readers. {location: trends answer, "suggestions":
    {location: …}, "wikipedia": …}. Touches no database."""
    from concurrent.futures import ThreadPoolExecutor

    from engine.evidence import search_sources

    asker = asker or search_sources.laddered()
    suggest = suggest or search_sources.suggestions
    readers = readers or search_sources.pageviews
    jobs = {loc: (lambda loc=loc: fetch(name, aliases, window_start, window_end, api_key,
                                        location=loc, now=now, asker=asker, keywords=keywords))
            for loc in locations}
    for loc in locations:
        jobs[f"suggest:{loc}"] = lambda loc=loc: suggest(name, loc)
    jobs["wikipedia"] = lambda: readers(name, aliases, window_start, window_end)
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {key: pool.submit(job) for key, job in jobs.items()}
        answers = {}
        for key, future in futures.items():
            try:
                answers[key] = future.result()
            except Exception as exc:  # noqa: BLE001 — one source must not cost the others
                answers[key] = {"state": "not_measured", "reason": f"{type(exc).__name__}: {exc}"[:200]}
    out = {loc: answers[loc] for loc in locations}
    out["suggestions"] = {loc: answers[f"suggest:{loc}"] for loc in locations}
    out["wikipedia"] = answers["wikipedia"]
    return out


def _asked_about(section: dict) -> list[dict]:
    """A place's searches that ask something, not the ones that only find
    the subject."""
    return [q for q in (section.get("rising_queries") or []) + (section.get("top_queries") or [])
            if (q.get("answer") or {}).get("state") != "navigational"]


def contrast(kenya: dict, world: dict) -> dict:
    """What the world searched about the subject that Kenya did not — the
    outside view, often the international angle — and what only Kenya
    searched. Compared as lists of searches, never as levels: each place's
    0-100 index is scaled to its own busiest moment, so Kenya's 40 and the
    world's 40 are not the same amount of searching."""
    if kenya.get("state") != "measured" or world.get("state") != "measured":
        return {}
    k = {q["query"].lower() for q in _asked_about(kenya)}
    w = {q["query"].lower() for q in _asked_about(world)}
    abroad = [q["query"] for q in _asked_about(world) if q["query"].lower() not in k]
    home = [q["query"] for q in _asked_about(kenya) if q["query"].lower() not in w]
    both = [q["query"] for q in _asked_about(kenya) if q["query"].lower() in w]
    return {"world_only": list(dict.fromkeys(abroad))[:6], "kenya_only": list(dict.fromkeys(home))[:6],
            "both": list(dict.fromkeys(both))[:6]}


def assemble(gathered: dict[str, dict], window_start: datetime, window_end: datetime, subject: str,
             coverage_by_day: dict | None = None, findings: list[dict] | None = None,
             storylines: list[dict] | None = None, corpus_texts: list[str] | None = None,
             previous: dict | None = None) -> dict:
    """The report's `search_demand`: Kenya read against the coverage, the
    world read for what it asks, and the contrast between them."""
    previous = previous or {}
    kenya = read(gathered.get("KE") or {"location": "KE"}, window_start, window_end, subject,
                 coverage_by_day, findings, storylines, corpus_texts, previous.get("kenya"))
    # The corpus is Kenyan coverage, so the world's curve is not timed
    # against it — a lead over Kenyan newsrooms says nothing about the world.
    world = read(gathered.get("") or {"location": ""}, window_start, window_end, subject,
                 None, findings, storylines, corpus_texts, previous.get("world"))
    subject_words = _terms(subject, set())
    for section, loc in ((kenya, "KE"), (world, "")):
        suggested = (gathered.get("suggestions") or {}).get(loc) or {}
        rows = []
        for row in suggested.get("suggestions") or []:
            rows.append({**row, "answer": answered(row["query"], subject_words, findings or [],
                                                   storylines or [], corpus_texts or [])})
        section["suggested"] = {"state": suggested.get("state", "not_measured"),
                                "reason": suggested.get("reason"),
                                "provider": suggested.get("provider") or "Google search suggestions",
                                "rows": rows[:20],
                                "unanswered": [r["query"] for r in rows
                                               if r["answer"]["state"] == "unanswered"][:8]}
    wikipedia = readers_section(gathered.get("wikipedia") or {}, window_start, window_end,
                                coverage_by_day, kenya)
    states = {kenya.get("state"), world.get("state"), wikipedia.get("state"),
              kenya["suggested"]["state"], world["suggested"]["state"]}
    return {"state": "measured" if "measured" in states else
            "too_little_search" if "too_little_search" in states else "not_measured",
            "kenya": kenya, "world": world, "contrast": contrast(kenya, world),
            "wikipedia": wikipedia}


def readers_section(raw: dict, window_start: datetime, window_end: datetime,
                    coverage_by_day: dict | None, kenya: dict) -> dict:
    """Wikipedia's readers as their own measure: real daily counts, of one
    article, worldwide. Timed against the coverage only when Kenya's search
    curve is missing — a second measure stands in, and says it is one."""
    out = {k: raw.get(k) for k in ("state", "reason", "title", "url", "provider", "total")}
    out["state"] = out.get("state") or "not_measured"
    series = [p for p in raw.get("series") or []
              if window_start.date().isoformat() <= p["date"] <= window_end.date().isoformat()]
    out["series"] = series
    if not series:
        return out
    peak = max(series, key=lambda p: p["views"])
    out["peak"] = peak
    out["total"] = sum(p["views"] for p in series)
    if not (kenya.get("series")) and coverage_by_day:
        daily = {p["date"]: float(p["views"]) for p in series}
        coverage = {str(k)[:10]: int(v or 0) for k, v in coverage_by_day.items()}
        days = sorted(daily)
        out["lead_lag"] = lead_lag(daily, coverage, days)
        out["stands_in_for_search"] = True
    return out


def all_signals(section: dict, subject: str) -> list[dict]:
    """Counted sentences from both places and the contrast."""
    section = section or {}
    out = signals(section.get("kenya") or {}, subject) + signals(section.get("world") or {}, subject)
    world = section.get("world") or {}
    kenya = section.get("kenya") or {}
    # Google's suggestions speak where Trends could not: the searches a
    # suggestion box offers, which nothing collected addresses.
    if not kenya.get("unanswered"):
        for query in ((kenya.get("suggested") or {}).get("unanswered") or [])[:2]:
            out.append({"kind": "search", "numbers": {},
                        "sentence": (f"Google suggests “{query}” to people in Kenya typing {subject}'s "
                                     f"name, and nothing this report collected addresses it."),
                        "url": f"https://www.google.com/search?q={quote(query)}&gl=ke"})
    wiki = section.get("wikipedia") or {}
    if wiki.get("state") == "measured" and wiki.get("peak"):
        out.append({"kind": "search", "url": wiki.get("url"),
                    "numbers": {"total": wiki["total"], "peak": wiki["peak"]["views"]},
                    "sentence": (f"The English Wikipedia article “{wiki.get('title')}” was read "
                                 f"{wiki['total']:,} times over the period, most on {wiki['peak']['date']} "
                                 f"({wiki['peak']['views']:,} reads).")})
    for query in ((section.get("contrast") or {}).get("world_only") or [])[:2]:
        out.append({"kind": "search",
                    "sentence": (f"“{query}” was among the searches about {subject} worldwide over "
                                 f"{world.get('timeframe_label') or 'the period'} but not among those in Kenya."),
                    "numbers": {},
                    "url": trends_url(query, world.get("timeframe"), "")})
    return out


# ---------------------------------------------------------------------------
# What the next run should ask
# ---------------------------------------------------------------------------

def leads(section: dict, most: int = 6) -> list[str]:
    """Searches nothing collected answers — and the rising ones the record
    does not hold — as questions for the NEXT run's collection.

    An unanswered search is either a gap in the coverage or a gap in what
    this engine collected; asking it is how the next report tells the two
    apart. Kenya first, then the world; suggestions after Trends' own
    searches, because a suggestion is a weaker signal than a measured rise.
    Navigational searches ("… news") ask nothing and are never leads."""
    out: list[str] = []
    for place in ("kenya", "world"):
        sec = (section or {}).get(place) or {}
        out.extend(sec.get("unanswered") or [])
        out.extend(q["query"] for q in sec.get("rising_queries") or []
                   if (q.get("answer") or {}).get("state") in ("unanswered", "in_coverage"))
    for place in ("kenya", "world"):
        out.extend(((section or {}).get(place) or {}).get("suggested", {}).get("unanswered") or [])
    seen, unique = set(), []
    for q in out:
        key = str(q).strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(str(q).strip())
    return unique[:most]
