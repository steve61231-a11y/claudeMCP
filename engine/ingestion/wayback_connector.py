"""Wayback Machine ingestion — historical recovery from the Internet Archive.

Two complementary uses of archive.org, both free and keyless:

1. `fetch()`: use the CDX index to enumerate archived captures of Kenyan news
   article URLs whose slug contains the politician's surname, across the
   window — a way to reconstruct historical coverage even from outlets that
   have since paywalled, restructured, or deleted the pages.

2. `recover(url)`: given a dead/blocked article URL (e.g. one GDELT returned
   whose original 404s), return the text of its most recent archived
   snapshot. Used by other connectors as an enrichment fallback so a historical
   link still yields readable content.

CDX and the availability API are simple keyless HTTP; mapping is unit-tested
against fixtures. Live fetches need open egress (the deployment).
"""

import re
from datetime import datetime

from engine.ingestion import http
from engine.ingestion.base import IngestedMention, IngestionConnector

CDX_URL = "http://web.archive.org/cdx/search/cdx"
AVAILABILITY_URL = "https://archive.org/wayback/available"
SNAPSHOT_URL = "https://web.archive.org/web/{timestamp}/{url}"

# Domains whose article slugs usually contain the person's name.
DEFAULT_DOMAINS = [
    "nation.africa", "standardmedia.co.ke", "the-star.co.ke",
    "citizen.digital", "kenyans.co.ke", "tuko.co.ke", "capitalfm.co.ke",
]
MAX_PER_DOMAIN = 40


class WaybackConnector(IngestionConnector):
    def __init__(self, domains: list[str] | None = None):
        self.domains = domains or DEFAULT_DOMAINS
        # Why the last fetch came back empty, when it came back empty. A
        # per-domain `except: continue` hides a blocked host, a timeout and a
        # genuine absence of captures behind the same silent zero.
        self.last_error: str | None = None

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        surname = politician_name.strip().split()[-1].lower()
        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        failures: list[str] = []
        for domain in self.domains:
            try:
                mentions.extend(self._cdx_domain(domain, surname, window_start, window_end, seen))
            except Exception as exc:  # noqa: BLE001 — one dead domain is not the run
                failures.append(f"{domain}: {type(exc).__name__}: {exc}"[:120])
                continue
        # Only a complete sweep of failures is worth reporting: archive.org
        # legitimately has no captures for some outlets, and calling that an
        # error would cry wolf on every run.
        self.last_error = "; ".join(failures[:3]) if len(failures) == len(self.domains) else None
        return mentions

    def _cdx_domain(self, domain, surname, window_start, window_end, seen) -> list[IngestedMention]:
        params = {
            "url": f"{domain}/*",
            "output": "json",
            "from": window_start.strftime("%Y%m%d"),
            "to": window_end.strftime("%Y%m%d"),
            "filter": f"original:.*(?i){re.escape(surname)}.*",
            "collapse": "urlkey",
            "limit": str(MAX_PER_DOMAIN),
        }
        response = http.get(CDX_URL, params=params, timeout=30)
        response.raise_for_status()
        rows = response.json()
        if not rows or len(rows) < 2:
            return []
        header, *data = rows  # first row is column names
        idx = {name: i for i, name in enumerate(header)}
        out: list[IngestedMention] = []
        for row in data:
            original = row[idx["original"]]
            timestamp = row[idx["timestamp"]]
            if original in seen or row[idx.get("statuscode", 0)] not in ("200", "-"):
                continue
            seen.add(original)
            posted = self._parse_ts(timestamp) or window_end
            title = self._title_from_slug(original)
            if not title:
                continue
            out.append(
                IngestedMention(
                    platform=domain,
                    source_type="article",
                    author_handle=domain,
                    text=title,
                    posted_at=posted,
                    engagement={},
                    raw_payload={
                        "url": original,
                        "archived_url": SNAPSHOT_URL.format(timestamp=timestamp, url=original),
                        "source": "wayback",
                    },
                )
            )
        return out

    def recover(self, url: str) -> str | None:
        """Text of the most recent archived snapshot of `url`, or None."""
        try:
            avail = http.get(AVAILABILITY_URL, params={"url": url}, timeout=20).json()
            snap = (avail.get("archived_snapshots") or {}).get("closest") or {}
            if not snap.get("available") or not snap.get("url"):
                return None
            # Fetch the archived page; strip HTML crudely to prose.
            html = http.get(snap["url"], timeout=30).text
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            return re.sub(r"\s+", " ", text).strip()[:4000] or None
        except Exception:
            return None

    @staticmethod
    def _title_from_slug(url: str) -> str:
        # Derive a human title from the URL slug: .../john-mbadi-defends-budget-123 -> "John Mbadi Defends Budget"
        m = re.search(r"/([^/?#]+)/?$", url)
        slug = m.group(1) if m else url
        slug = re.sub(r"\.(html?|php|aspx?)$", "", slug)
        slug = re.sub(r"[-_]+\d+$", "", slug)  # trailing article id
        words = [w for w in re.split(r"[-_]+", slug) if w and not w.isdigit()]
        return " ".join(w.capitalize() for w in words) if len(words) >= 2 else ""

    @staticmethod
    def _parse_ts(value) -> datetime | None:
        try:
            return datetime.strptime(str(value)[:14], "%Y%m%d%H%M%S")
        except (ValueError, TypeError):
            return None
