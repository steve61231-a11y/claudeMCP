"""Can Mũũgĩ read Tuko (tuko.co.ke) directly, and how? Observed, not assumed.

Asks, from GitHub's network, every route a connector could use — robots.txt
(what Tuko allows), its RSS feeds, its own search page, its sitemaps, a
Google News query restricted to the site — and reads one article for the
fields a connector needs (headline, date, body). Public topics only.

    python scripts/tuko_probe.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}
BASE = "https://www.tuko.co.ke"


def show(label: str, url: str, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=25, **kw)
    except Exception as exc:  # noqa: BLE001
        print(f"\n--- {label}: {type(exc).__name__}: {exc}"[:300])
        return None
    print(f"\n--- {label}: HTTP {r.status_code} · {r.headers.get('content-type')} · {len(r.content)} bytes · {r.url}")
    return r


def main() -> int:
    r = show("robots.txt", f"{BASE}/robots.txt")
    if r is not None and r.ok:
        lines = [l for l in r.text.splitlines() if l.strip() and not l.startswith("#")]
        print("\n".join(f"   {l}" for l in lines[:60]))

    for path in ("/rss/all.rss", "/rss/politics.rss", "/rss/kenya.rss", "/feed", "/rss"):
        r = show(f"RSS {path}", BASE + path)
        if r is not None and r.ok and b"<item" in r.content:
            items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
            print(f"   items: {len(items)}")
            for it in items[:3]:
                t = re.search(r"<title>(.*?)</title>", it, re.S)
                d = re.search(r"<pubDate>(.*?)</pubDate>", it)
                l = re.search(r"<link>(.*?)</link>", it, re.S)
                print(f"   · {(t.group(1) if t else '')[:110]!r} | {d.group(1) if d else None} | {l.group(1).strip() if l else None}")

    for q in ("/search/?query=Raila", "/search/?q=Raila", "/search?query=Raila"):
        r = show(f"site search {q}", BASE + q)
        if r is not None and r.ok:
            links = sorted(set(re.findall(r'href="(https://www\.tuko\.co\.ke/[a-z0-9-]+/\d+[^"]*)"', r.text)))
            print(f"   article links found: {len(links)}; first: {links[:3]}")

    for path in ("/sitemap.xml", "/sitemap_news.xml", "/news-sitemap.xml", "/sitemaps/news.xml"):
        r = show(f"sitemap {path}", BASE + path)
        if r is not None and r.ok:
            locs = re.findall(r"<loc>(.*?)</loc>", r.text)
            print(f"   locs: {len(locs)}; first: {locs[:3]}")

    gq = urllib.parse.quote("site:tuko.co.ke Raila when:30d")
    r = show("Google News, site:tuko.co.ke", f"https://news.google.com/rss/search?q={gq}&hl=en-KE&gl=KE&ceid=KE:en")
    if r is not None and r.ok:
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
        print(f"   items: {len(items)}")
        for it in items[:3]:
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            d = re.search(r"<pubDate>(.*?)</pubDate>", it)
            print(f"   · {(t.group(1) if t else '')[:110]!r} | {d.group(1) if d else None}")

    # One article: what a connector can read from it.
    r = show("RSS for an article", BASE + "/rss/all.rss")
    link = None
    if r is not None and r.ok:
        m = re.search(r"<link>(https://www\.tuko\.co\.ke/[^<]+)</link>", r.text)
        link = m.group(1).strip() if m else None
    if link:
        a = show("article", link)
        if a is not None and a.ok:
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', a.text, re.S)[:4]:
                try:
                    data = json.loads(block)
                except Exception:  # noqa: BLE001
                    continue
                for d in data if isinstance(data, list) else [data]:
                    if isinstance(d, dict):
                        print(f"   JSON-LD {d.get('@type')}: headline={str(d.get('headline'))[:80]!r} "
                              f"published={d.get('datePublished')} body_chars={len(str(d.get('articleBody') or ''))} "
                              f"author={str(d.get('author'))[:60]}")
            paras = re.findall(r"<p[^>]*>(.*?)</p>", a.text, re.S)
            print(f"   <p> paragraphs: {len(paras)}; text chars ≈ {sum(len(re.sub('<[^>]+>', '', p)) for p in paras)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
