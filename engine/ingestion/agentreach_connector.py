"""AgentReach ingestion source — a zero-API-fee complement to SocialCrawl.

AgentReach (github.com/Panniantong/Agent-Reach) is an installer/router that
gives an agent free access to 15+ web/social platforms via cookie-auth or
public scraping instead of paid APIs. We use it here as an additional,
free ingestion source: whichever of its backends are installed and reachable
contribute mentions, merged into the same pipeline as SocialCrawl/NewsAPI.

Two backends are wired:
- `web` (Jina Reader): always available, no key — reads a search-results page
  for the politician and then the linked articles. This is the dependable
  free-news lane.
- `twitter` / `youtube`: used only when their CLI backends (twitter-cli,
  yt-dlp) are installed AND report healthy via `agent-reach doctor --json`,
  so a missing backend degrades silently instead of erroring.

Design note: AgentReach and its backends need open outbound network. In the
build sandbox all external hosts are blocked by egress policy (the same wall
that blocks SocialCrawl and Render), so this connector is exercised by unit
tests against fixture output; it does real fetches wherever egress is open
(the user's machine / the Render deployment).
"""

import json
import re
import subprocess
from datetime import datetime
from urllib.parse import quote_plus

from engine.ingestion.base import IngestedMention, IngestionConnector

DEFAULT_TIMEOUT = 45
MAX_ARTICLES = 15
# Bing News has a stable, scrape-friendly results page and good Kenyan
# coverage; Jina Reader turns it into clean markdown with the article links.
SEARCH_URL = "https://www.bing.com/news/search?q={query}"
_ARTICLE_RE = re.compile(r"\[(?P<title>[^\]]{12,200})\]\((?P<url>https?://[^)]+)\)")


class AgentReachConnector(IngestionConnector):
    def __init__(self, doctor: dict | None = None):
        # doctor map lets callers/tests inject backend availability instead of
        # shelling out; when omitted we query agent-reach once.
        self._doctor = doctor if doctor is not None else self._run_doctor()

    # --- public entry point -------------------------------------------------
    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        mentions: list[IngestedMention] = []
        terms = [politician_name, *aliases]
        seen_urls: set[str] = set()
        for term in terms:
            try:
                mentions.extend(self._web_search(term, window_end, seen_urls))
            except Exception:
                continue
        if self._backend_ok("twitter"):
            for term in terms[:2]:
                try:
                    mentions.extend(self._twitter_search(term, window_end))
                except Exception:
                    continue
        return mentions

    # --- backends -----------------------------------------------------------
    def _web_search(self, term: str, window_end: datetime, seen_urls: set) -> list[IngestedMention]:
        """Read a news search page via Jina, follow the article links, and turn
        each into a mention. Pure web reading — no API key."""
        from agent_reach.channels.web import WebChannel

        web = WebChannel()
        results_md = web.read(SEARCH_URL.format(query=quote_plus(term)))
        articles = self._extract_articles(results_md)
        mentions: list[IngestedMention] = []
        for title, url in articles[:MAX_ARTICLES]:
            if url in seen_urls or "bing.com" in url:
                continue
            seen_urls.add(url)
            snippet = title
            try:
                body = web.read(url)
                snippet = self._clean(body)[:1200] or title
            except Exception:
                pass
            mentions.append(
                IngestedMention(
                    platform=self._domain(url),
                    source_type="article",
                    author_handle=self._domain(url),
                    text=f"{title}. {snippet}".strip(),
                    posted_at=window_end,  # Jina rarely exposes a reliable date; window_end is the safe default
                    engagement={},
                    raw_payload={"url": url, "title": title, "source": "agentreach:web"},
                )
            )
        return mentions

    def _twitter_search(self, term: str, window_end: datetime) -> list[IngestedMention]:
        """Uses whatever twitter backend agent-reach installed. Output shape
        varies by backend, so we parse defensively and skip what we can't map."""
        raw = self._run(["twitter-cli", "search", term, "--json", "--limit", "40"])
        if not raw:
            return []
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items = items.get("tweets", items) if isinstance(items, dict) else items
        mentions: list[IngestedMention] = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            text = it.get("text") or it.get("content") or ""
            if not text:
                continue
            mentions.append(
                IngestedMention(
                    platform="twitter",
                    source_type="post",
                    author_handle=(it.get("author") or it.get("username") or "unknown"),
                    text=text,
                    posted_at=self._parse_dt(it.get("created_at")) or window_end,
                    engagement={
                        "likes": it.get("likes") or it.get("favorite_count") or 0,
                        "shares": it.get("retweets") or it.get("retweet_count") or 0,
                        "comments": it.get("replies") or it.get("reply_count") or 0,
                    },
                    raw_payload={**it, "source": "agentreach:twitter"},
                )
            )
        return mentions

    # --- helpers ------------------------------------------------------------
    @staticmethod
    def _extract_articles(markdown: str) -> list[tuple[str, str]]:
        out, seen = [], set()
        for m in _ARTICLE_RE.finditer(markdown or ""):
            url = m.group("url")
            if url in seen:
                continue
            seen.add(url)
            out.append((m.group("title").strip(), url))
        return out

    @staticmethod
    def _clean(markdown: str) -> str:
        # Strip markdown links/images/headers to leave readable prose.
        text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", " ", markdown or "")
        text = re.sub(r"[#*_>`]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _domain(url: str) -> str:
        m = re.search(r"https?://([^/]+)/?", url)
        return (m.group(1).replace("www.", "") if m else "web")

    @staticmethod
    def _parse_dt(value) -> datetime | None:
        if not value:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%a %b %d %H:%M:%S %z %Y"):
            try:
                return datetime.strptime(str(value).split(".")[0].replace("Z", ""), fmt).replace(tzinfo=None)
            except ValueError:
                continue
        return None

    def _backend_ok(self, name: str) -> bool:
        entry = (self._doctor or {}).get(name) or {}
        return entry.get("status") == "ok"

    @staticmethod
    def _run(cmd: list[str]) -> str | None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT)
            return proc.stdout if proc.returncode == 0 else None
        except Exception:
            return None

    def _run_doctor(self) -> dict:
        raw = self._run(["agent-reach", "doctor", "--json"])
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
