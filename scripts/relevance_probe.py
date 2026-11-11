"""Is what a name-search returns actually about the name? Measured, not assumed.

The Search report links every GDELT / Google News result to its subject
without reading a word of it ("on-topic by construction"), and the founder
saw links to Russian newspapers in a client's report. This runs the REAL
planned queries for public subjects against the live sources and counts,
per query: how many results are in a language the engine does not read,
where their outlets are, and how many headlines name the subject at all.
Then it runs the candidate fixes, so a change is chosen on numbers.

Public subjects only (the head of state, a ministry, a listed company) —
never anyone a client is researching; the repository and its logs are public.

    PYTHONPATH=. python scripts/relevance_probe.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace

from engine.ingestion import queries
from engine.ingestion.gdelt_connector import GdeltConnector
from engine.ingestion.google_news_rss_connector import GoogleNewsRssConnector

SUBJECTS = [
    ("William Ruto", "politician"),
    ("Ministry of Health", "ministry"),
    ("Safaricom", "business"),
]


def subject(name: str, kind: str):
    """A subject as `api_server._ensure_politician` creates one from a search."""
    if kind in ("person", "politician", "individual"):
        last = name.split()[-1]
        aliases = [name, f"Hon. {name}", f"Sen. {last}", f"Honorable {name}"]
    else:
        aliases = [name]
    return SimpleNamespace(name=name, aliases=aliases, titles=[], swahili_terms=[], tracked_hashtags=[],
                           subject_type=kind, investigation_leads=None, keywords=[])


def words(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", (text or "").lower())) + " "


def names(title: str, name: str, kind: str) -> str:
    folded = words(title)
    if words(name) in folded:
        return "full name"
    if kind in ("person", "politician", "individual"):
        last = name.split()[-1]
        if len(last) >= 4 and words(last) in folded:
            return "surname"
    return "not named"


def non_latin(text: str) -> bool:
    letters = [c for c in text or "" if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if ord(c) > 0x24F) / len(letters) > 0.3


def report(label: str, items: list, name: str, kind: str, gdelt: bool) -> None:
    if not items:
        print(f"   {label}: 0 items")
        return
    named = Counter(names(m["text"], name, kind) for m in items)
    line = (f"   {label}: {len(items)} items · full name {named['full name']} · surname {named['surname']} · "
            f"NOT named {named['not named']} · non-Latin script {sum(non_latin(m['text']) for m in items)}")
    print(line)
    if gdelt:
        langs = Counter((m["raw_payload"].get("language") or "?") for m in items)
        lands = Counter((m["raw_payload"].get("sourcecountry") or "?") for m in items)
        print(f"      languages {langs.most_common(6)}")
        print(f"      outlet countries {lands.most_common(8)}")
        russian = [m for m in items if (m["raw_payload"].get("sourcecountry") or "") == "Russia"
                   or (m["raw_payload"].get("language") or "") == "Russian"]
        for m in russian[:3]:
            print(f"      RUSSIA: {m['platform']} [{m['raw_payload'].get('language')}] {m['text'][:110]!r}")
    for m in [m for m in items if names(m["text"], name, kind) == "not named"][:3]:
        print(f"      not named: {m['platform'][:28]} | {m['text'][:120]!r}")


def main() -> int:
    we = datetime.utcnow()
    ws = we - timedelta(days=30)
    gdelt, gnews = GdeltConnector(), GoogleNewsRssConnector()
    for name, kind in SUBJECTS:
        who = subject(name, kind)
        print(f"\n=== {name} ({kind})")
        for source, connector, is_gdelt in (("gdelt", gdelt, True), ("google_news", gnews, False)):
            planned = queries.connector_variants(who, source)
            print(f"-- {source}: {len(planned)} planned queries")
            everything = []
            for query in planned:
                connector.last_error = None
                found = connector.fetch(name, who.aliases, ws, we, query=query)
                if getattr(connector, "last_error", None):
                    print(f"   {query!r}: ERROR {connector.last_error}")
                    continue
                report(repr(query), found, name, kind, is_gdelt)
                everything += found
            unique = list({(m["raw_payload"].get("url") or m["text"]): m for m in everything}.values())
            report(f"ALL {source} (unique)", unique, name, kind, is_gdelt)
        # The candidate fixes for GDELT, on the bare name.
        print("-- gdelt candidates")
        for label, query in (
            ("quoted", f'"{name}"'),
            ("quoted + English", f'"{name}" sourcelang:english'),
            ("quoted + English or Swahili", f'"{name}" (sourcelang:english OR sourcelang:swahili)'),
            ("quoted + Kenyan outlets", f'"{name}" sourcecountry:kenya'),
            ("quoted + names Kenya", f'"{name}" (kenya OR kenyan OR nairobi)'),
        ):
            gdelt.last_error = None
            found = gdelt.fetch(name, who.aliases, ws, we, query=query)
            if getattr(gdelt, "last_error", None):
                print(f"   {label} {query!r}: ERROR {gdelt.last_error}")
                continue
            report(f"{label} {query!r}", found, name, kind, True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
