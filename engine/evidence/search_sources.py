"""Keyless sources of search demand, so no single provider holds the feature up.

SocialCrawl's Trends endpoints are the dependable route — a paid, maintained
API — and the founder's rule is that no one provider may be a single point of
failure: an unpaid account, an outage or a changed contract must cost a
measurement, never the section, and never read as "nobody searched".

So each signal has a ladder, and every answer says which rung gave it:

- **Google Trends, asked directly.** The same data SocialCrawl resells, from
  Google's own (undocumented, unkeyed) endpoints: an explore request yields
  widget tokens, each token one dataset. Google rate-limits cloud addresses
  hard, so a 429 is remembered for the host (`http.remember_refusal`) rather
  than rediscovered on every report. Returned in SocialCrawl's shape, so one
  reader serves both.
- **Google's search suggestions** (`suggestqueries`) for Kenya and the world:
  what Google offers as someone types. Not volumes — a ranked list Google
  also filters — and labelled so; but keyless, lenient, and the closest free
  view of what people actually type.
- **Wikipedia page views**: an official, keyless API with real daily counts.
  A different measure — readers of one article, worldwide — so it is shown as
  itself and never passed off as searches.

Written without network access from the development sandbox; the request
shapes follow the documented behaviour of these endpoints, and
`.github/workflows/source-probe.yml` asks them from GitHub's network. Every
answer this file cannot parse is reported as not measured with what arrived.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import quote

from engine.evidence import search_demand as sd

TRENDS_API = "https://trends.google.com/trends/api"
TRENDS_HOME = "https://trends.google.com/"
SUGGEST = "https://suggestqueries.google.com/complete/search"
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"

#: How long a 429 from Google Trends keeps this server from asking again.
TRENDS_REFUSAL_HOURS = 2.0


def _json_after_prefix(text: str) -> dict:
    """Google prefixes these answers with `)]}'` to defeat JSON hijacking."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:])


def _refusal(url: str, status: int) -> str:
    from engine.ingestion import http

    reason = (f"Google Trends refused this server (HTTP {status}); it rate-limits requests from "
              "cloud addresses. Not asked again for a while.")
    if status in (403, 429):
        http.remember_refusal(url, reason, hours=TRENDS_REFUSAL_HOURS)
    return reason


_warmed = False


def _warm() -> None:
    """One visit to the Trends home page for the cookie its API expects."""
    global _warmed
    if _warmed:
        return
    from engine.ingestion import http

    try:
        http.get(TRENDS_HOME, params={"geo": "KE"}, timeout=15)
    except Exception:  # noqa: BLE001 — the API call that follows will say what is wrong
        pass
    _warmed = True


def direct_ask(api_key: str, kind: str, params: dict) -> dict:
    """The SocialCrawl `ask` contract, answered by Google Trends itself.
    `api_key` is ignored; the signature matches so the ladder can swap rungs."""
    from engine.ingestion import http

    preset = params.get("timeframe") or "past_30_days"
    geo = params.get("location") or ""
    keywords = (params.get("keywords") or "").split(",") if kind == "explore" else [params.get("keyword") or ""]
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        return {"state": "not_measured", "reason": "no keyword to ask about", "data": None}
    req = {"comparisonItem": [{"keyword": k, "time": sd.TRENDS_DATE.get(preset, "today 1-m"), "geo": geo}
                              for k in keywords], "category": 0, "property": ""}
    url = f"{TRENDS_API}/explore"
    try:
        _warm()
        response = http.get(url, params={"hl": "en-US", "tz": "0", "req": json.dumps(req)}, timeout=30)
        if response.status_code != 200:
            return {"state": "not_measured", "reason": _refusal(url, response.status_code), "data": None}
        widgets = _json_after_prefix(response.text).get("widgets") or []
        wanted = "TIMESERIES" if kind == "explore" else "RELATED_QUERIES"
        widget = next((w for w in widgets if str(w.get("id", "")).startswith(wanted)), None)
        if widget is None:
            return {"state": "not_measured",
                    "reason": f"Google Trends sent no {wanted} widget (got: "
                              + ", ".join(str(w.get("id")) for w in widgets)[:120] + ")", "data": None}
        path = "multiline" if kind == "explore" else "relatedsearches"
        data_url = f"{TRENDS_API}/widgetdata/{path}"
        answer = http.get(data_url, params={"hl": "en-US", "tz": "0", "req": json.dumps(widget["request"]),
                                            "token": widget["token"]}, timeout=30)
        if answer.status_code != 200:
            return {"state": "not_measured", "reason": _refusal(data_url, answer.status_code), "data": None}
        body = _json_after_prefix(answer.text).get("default") or {}
    except Exception as exc:  # noqa: BLE001 — a refusal or a changed shape is not "no searches"
        return {"state": "not_measured", "reason": f"Google Trends: {type(exc).__name__}: {exc}"[:220],
                "data": None}
    if kind == "explore":
        return _timeline(body, keywords)
    return _related(body)


def _timeline(body: dict, keywords: list[str]) -> dict:
    rows = body.get("timelineData") or []
    if not rows or not any(any(r.get("hasData") or [True]) for r in rows):
        return {"state": "too_little_search", "reason": None, "data": None}
    series = [{"keyword": k, "points": []} for k in keywords]
    for row in rows:
        try:
            at = datetime.utcfromtimestamp(int(row.get("time")))
        except (TypeError, ValueError):
            continue
        values = row.get("value") or []
        for i, s in enumerate(series):
            if i < len(values):
                s["points"].append({"date": at.date().isoformat(), "datetime": at.isoformat() + "Z",
                                    "value": values[i], "partial": bool(row.get("isPartial"))})
    averages = body.get("averages") or [
        round(sum(p["value"] for p in s["points"]) / len(s["points"])) if s["points"] else 0 for s in series]
    return {"state": "measured", "reason": None,
            "data": {"series": series,
                     "averages": [{"keyword": k, "value": averages[i] if i < len(averages) else 0}
                                  for i, k in enumerate(keywords)]}}


def _related(body: dict) -> dict:
    ranked = body.get("rankedList") or []
    if not ranked:
        return {"state": "too_little_search", "reason": None, "data": None}

    def rows(i):
        return (ranked[i].get("rankedKeyword") or []) if len(ranked) > i else []

    top = [{"query": r.get("query"), "value": r.get("value")} for r in rows(0)]
    rising = [{"query": r.get("query"), "value": r.get("value"), "formatted_value": r.get("formattedValue")}
              for r in rows(1)]
    if not top and not rising:
        return {"state": "too_little_search", "reason": None, "data": None}
    return {"state": "measured", "reason": None, "data": {"top": top, "rising": rising}}


def laddered(socialcrawl_ask=None, direct=None):
    """An `ask` that tries SocialCrawl, then Google Trends directly, and says
    which answered. "Too few searches" from the first rung IS an answer and is
    not second-guessed; only a failure to measure moves down the ladder."""
    socialcrawl_ask = socialcrawl_ask or sd.ask
    direct = direct or direct_ask

    def ask(api_key: str, kind: str, params: dict) -> dict:
        first = socialcrawl_ask(api_key, kind, params) if api_key else {
            "state": "not_measured", "reason": "SOCIALCRAWL_API_KEY is not set", "data": None}
        if first["state"] != "not_measured":
            return {**first, "provider": "SocialCrawl (Google Trends)"}
        second = direct(api_key, kind, params)
        return {**second, "provider": "Google Trends (direct)",
                "fallback_from": {"provider": "SocialCrawl", "reason": first.get("reason")}}

    return ask


# ---------------------------------------------------------------------------
# What Google suggests
# ---------------------------------------------------------------------------

#: The ways a question about someone starts or ends. Seven requests per place,
#: not the 26-letter alphabet sweep: a name's suggestions saturate quickly,
#: and each request is a request to a host that can refuse.
PROMPTS = ("{name}", "{name} is", "is {name}", "why {name}", "who is {name}", "{name} and", "{name} vs")


def suggestions(name: str, location: str = "KE") -> dict:
    """{"state", "reason", "suggestions": [{"query", "prompt"}]} — what Google
    offers as someone types the name, in Kenya or anywhere."""
    from engine.ingestion import http

    seen, out, errors = set(), [], []
    for prompt in PROMPTS:
        q = prompt.format(name=name)
        params = {"client": "firefox", "hl": "en", "q": q}
        if location:
            params["gl"] = location.lower()
        try:
            response = http.get(SUGGEST, params=params, timeout=10)
            if response.status_code != 200:
                errors.append(f"HTTP {response.status_code}")
                continue
            body = json.loads(response.content.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            errors.append(type(exc).__name__)
            continue
        for s in (body[1] if isinstance(body, list) and len(body) > 1 else []) or []:
            s = str(s).strip()
            if s and s.lower() != q.lower() and s.lower() not in seen:
                seen.add(s.lower())
                out.append({"query": s, "prompt": q})
    if out:
        return {"state": "measured", "reason": None, "suggestions": out[:25],
                "provider": "Google search suggestions"}
    if errors and len(errors) == len(PROMPTS):
        return {"state": "not_measured", "reason": "Google suggestions: " + ", ".join(sorted(set(errors))),
                "suggestions": []}
    return {"state": "too_little_search", "reason": None, "suggestions": []}


# ---------------------------------------------------------------------------
# Wikipedia readers
# ---------------------------------------------------------------------------

def pageviews(name: str, aliases: list[str] | None, window_start: datetime, window_end: datetime) -> dict:
    """Daily readers of the subject's English Wikipedia article over the
    period: {"state", "title", "url", "series": [{"date", "views"}], "total"}.
    The article is the one Wikipedia's own search ranks first, and its title
    and link are carried, so a reader can see which page was counted."""
    from engine.ingestion import http
    from engine.ingestion.wikipedia_connector import WikipediaConnector

    connector = WikipediaConnector()
    title = connector._resolve_title(name, aliases or [])
    if not title:
        return {"state": "not_measured",
                "reason": getattr(connector, "last_error", None) or "no Wikipedia article was found",
                "series": []}
    article = quote(title.replace(" ", "_"), safe="")
    start = window_start.strftime("%Y%m%d")
    end = (window_end - timedelta(seconds=1)).strftime("%Y%m%d")
    try:
        response = http.get(f"{PAGEVIEWS}/{article}/daily/{start}/{end}", timeout=20)
    except Exception as exc:  # noqa: BLE001
        return {"state": "not_measured", "reason": f"Wikipedia: {type(exc).__name__}", "series": [],
                "title": title}
    if response.status_code == 404:
        return {"state": "too_little_search", "reason": None, "series": [], "title": title}
    if response.status_code != 200:
        return {"state": "not_measured", "reason": f"Wikipedia answered HTTP {response.status_code}",
                "series": [], "title": title}
    items = (response.json() or {}).get("items") or []
    series = [{"date": f"{str(i['timestamp'])[:4]}-{str(i['timestamp'])[4:6]}-{str(i['timestamp'])[6:8]}",
               "views": int(i.get("views") or 0)} for i in items if i.get("timestamp")]
    return {"state": "measured" if series else "too_little_search", "reason": None,
            "title": title, "url": f"https://en.wikipedia.org/wiki/{article}",
            "series": series, "total": sum(p["views"] for p in series),
            "provider": "Wikipedia page views (Wikimedia)"}
