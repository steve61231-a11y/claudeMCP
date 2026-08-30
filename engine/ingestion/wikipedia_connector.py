"""Wikipedia connector — authoritative background context for any subject.

Social scrapers and news give us the *now*; Wikipedia gives the *baseline* — who
the subject is, their roles, affiliations and the entities they're linked to.
Feeding that into the corpus grounds the analysis (the model knows Mbadi is the
Treasury CS, not a random name) and seeds the relationship graph with real,
sourced connections.

Implementation note: the popular `wikipedia` PyPI package won't build in this
environment (broken Debian setuptools `install_layout`), and an MCP Wikipedia
server is another moving part to host. Both ultimately wrap the same public,
keyless MediaWiki REST + Action APIs — so we call those directly through our
shared retrying `http` session. No API key, no extra dependency, no browser.

Maps to IngestedMention with platform="wikipedia", source_type="reference":
one mention for the subject's own article summary/extract, plus one per linked
entity summary (bounded), so linked-entity background enters the corpus too.
"""

from datetime import datetime

from engine.config import settings
from engine.ingestion import http
from engine.ingestion.base import IngestedMention, IngestionConnector

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_REST_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
MAX_LINKED_ENTITIES = 8
EXTRACT_MAX_CHARS = 6000


class WikipediaConnector(IngestionConnector):
    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        if not settings.enable_wikipedia:
            return []
        title = self._resolve_title(politician_name, aliases)
        if not title:
            return []

        mentions: list[IngestedMention] = []
        extract, links = self._fetch_page(title)
        if extract:
            mentions.append(self._as_mention(title, extract, window_end, relation="subject"))

        for linked in links[:MAX_LINKED_ENTITIES]:
            summary = self._fetch_summary(linked)
            if summary:
                mentions.append(self._as_mention(linked, summary, window_end, relation="linked_entity"))
        return mentions

    # --- MediaWiki calls (keyless) -------------------------------------------

    def _resolve_title(self, name: str, aliases: list[str]) -> str | None:
        """Search for the best-matching article title for the subject."""
        params = {
            "action": "query",
            "list": "search",
            "srsearch": name,
            "srlimit": "1",
            "format": "json",
        }
        try:
            resp = http.get(WIKI_API, params=params, timeout=20)
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search", [])
        except Exception as exc:  # noqa: BLE001
            # This is the FIRST thing the connector does, so a failure here
            # bails the whole source before any other handler is reached.
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return None
        if hits:
            return hits[0].get("title")
        return name

    def _fetch_page(self, title: str) -> tuple[str, list[str]]:
        """Return (intro extract, list of linked article titles) for `title`."""
        params = {
            "action": "query",
            "prop": "extracts|links",
            "titles": title,
            "explaintext": "1",
            "exintro": "0",
            "pllimit": "50",
            "plnamespace": "0",
            "format": "json",
        }
        try:
            resp = http.get(WIKI_API, params=params, timeout=25)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return "", []
        for page in pages.values():
            extract = (page.get("extract") or "").strip()[:EXTRACT_MAX_CHARS]
            links = [l.get("title") for l in (page.get("links") or []) if l.get("title")]
            return extract, links
        return "", []

    def _fetch_summary(self, title: str) -> str:
        try:
            resp = http.get(WIKI_REST_SUMMARY + title.replace(" ", "_"), timeout=15)
            if resp.status_code != 200:
                return ""
            return (resp.json().get("extract") or "").strip()[:1500]
        except Exception:
            return ""

    def _as_mention(self, title: str, text: str, posted_at: datetime, relation: str) -> IngestedMention:
        return IngestedMention(
            platform="wikipedia",
            source_type="reference",
            author_handle="wikipedia",
            text=text if text.startswith(title) else f"{title} — {text}",
            posted_at=posted_at,
            engagement={},
            raw_payload={
                "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "title": title,
                "relation": relation,
                "source": "wikipedia",
            },
        )
