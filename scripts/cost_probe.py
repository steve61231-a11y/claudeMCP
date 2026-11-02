"""What a scan actually costs — measured, for the pricing decision.

Prints three things, all public-safe (no subject names, no hostnames):
  1. OpenRouter's live price list for every DeepSeek model;
  2. the exchange rates for pounds and euros into dollars;
  3. from the live database: model tokens used per day (llm_usage) beside
     the number of Search reports and Issue Maps run that day, so tokens per
     scan are measured rather than estimated.

    python scripts/cost_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "muugi-cost-probe"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def prices() -> None:
    print("\n=== OpenRouter: DeepSeek models (USD per million tokens)")
    try:
        models = get("https://openrouter.ai/api/v1/models")["data"]
    except Exception as exc:  # noqa: BLE001
        print(f"   could not read: {type(exc).__name__}: {exc}")
        return
    rows = []
    for m in models:
        if not m["id"].startswith("deepseek/"):
            continue
        p = m.get("pricing") or {}
        per_m = lambda k: round(float(p.get(k) or 0) * 1e6, 4)  # noqa: E731
        created = datetime.fromtimestamp(m.get("created") or 0, tz=timezone.utc).date()
        rows.append((created, m["id"], per_m("prompt"), per_m("completion"), per_m("input_cache_read"),
                     m.get("context_length"), (m.get("description") or "")[:140].replace("\n", " ")))
    for r in sorted(rows, reverse=True):
        print(f"   {r[0]} | {r[1]:<44} | in ${r[2]:<8} | out ${r[3]:<8} | cache ${r[4]:<7} | ctx {r[5]} | {r[6]}")


def rates() -> None:
    print("\n=== Exchange rates into USD")
    for cur in ("GBP", "EUR"):
        try:
            print(f"   1 {cur} = {get(f'https://open.er-api.com/v6/latest/{cur}')['rates']['USD']} USD")
        except Exception as exc:  # noqa: BLE001
            print(f"   {cur}: could not read: {type(exc).__name__}")


def usage() -> None:
    print("\n=== Live database: model tokens per day beside scans run that day")
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("   DATABASE_URL not set")
        return
    try:
        import psycopg2

        conn = psycopg2.connect(url.replace("postgresql+psycopg2://", "postgresql://"), connect_timeout=20)
    except Exception as exc:  # noqa: BLE001
        print(f"   could not connect: {type(exc).__name__}")
        return
    cur = conn.cursor()

    def q(sql):
        try:
            cur.execute(sql)
            return cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"   query failed: {type(exc).__name__}: {str(exc)[:120]}")
            return []

    tokens = {r[0]: r[1:] for r in q("SELECT day, calls, input_tokens, output_tokens FROM llm_usage")}
    reports = dict(q("SELECT generated_at::date, count(*) FROM intelligence_reports GROUP BY 1"))
    maps = dict(q("SELECT started_at::date, count(*) FROM run_progress WHERE kind='issue_map' GROUP BY 1"))
    runs = dict(q("SELECT started_at::date, count(*) FROM ingestion_runs GROUP BY 1"))
    print("   day        | calls | input tokens | output tokens | search reports | issue maps | ingestion runs")
    tot = [0, 0, 0, 0, 0]
    for day in sorted(set(tokens) | set(reports) | set(maps)):
        c, i, o = tokens.get(day, (0, 0, 0))
        r, m = reports.get(day, 0), maps.get(day, 0)
        tot = [tot[0] + c, tot[1] + i, tot[2] + o, tot[3] + r, tot[4] + m]
        print(f"   {day} | {c:>5} | {i:>12,} | {o:>13,} | {r:>14} | {m:>10} | {runs.get(day, 0)}")
    print(f"   TOTAL      | {tot[0]:>5} | {tot[1]:>12,} | {tot[2]:>13,} | {tot[3]:>14} | {tot[4]:>10}")
    # Days with exactly one scan and tokens: the cleanest per-scan measurements.
    print("\n   Days with exactly one scan (tokens that day = that scan + its back office):")
    for day in sorted(tokens):
        r, m = reports.get(day, 0), maps.get(day, 0)
        if r + m == 1 and tokens[day][1]:
            print(f"   {day} | {'search report' if r else 'issue map':<13} | in {tokens[day][1]:,} | "
                  f"out {tokens[day][2]:,} | calls {tokens[day][0]}")
    # Which Kenyan newsrooms already reach the corpus, by stored link (counts only).
    print("\n   Items already stored per Kenyan newsroom (by link):")
    for host in ("tuko.co.ke", "nation.africa", "standardmedia.co.ke", "the-star.co.ke", "citizen.digital",
                 "kenyans.co.ke", "capitalfm.co.ke", "businessdailyafrica.com", "kbc.co.ke"):
        n = q(f"SELECT count(*) FROM raw_mentions WHERE raw_payload->>'url' ILIKE '%{host}%' "
              f"OR raw_payload->>'source_url' ILIKE '%{host}%'")
        print(f"   {host:<26} {n[0][0] if n else '?'}")
    n = q("SELECT count(*) FROM raw_mentions WHERE author_handle ILIKE '%tuko%'")
    print(f"   items whose publisher name is Tuko: {n[0][0] if n else '?'}")
    size = q("SELECT pg_database_size(current_database())")
    if size:
        print(f"\n   database size: {size[0][0] / 1e9:.2f} GB")
    counts = q("SELECT (SELECT count(*) FROM raw_mentions), (SELECT count(*) FROM documents), "
               "(SELECT count(*) FROM intelligence_reports)")
    if counts:
        print(f"   rows: {counts[0][0]:,} mentions, {counts[0][1]:,} documents, {counts[0][2]:,} reports")


def live() -> None:
    """The live server's model settings and memory, from its public health
    endpoint — model names only; it never reports any part of a key."""
    print("\n=== Live server: which models it runs")
    url = os.environ.get("ENGINE_URL") or "https://muugi-production-8e01.up.railway.app"
    try:
        h = get(f"{url}/api/health")
    except Exception as exc:  # noqa: BLE001
        print(f"   could not read: {type(exc).__name__}")
        return
    llm = h.get("llm") or {}
    for k in ("backend", "provider", "model", "bulk_model", "production", "production_grade", "key_present"):
        if k in llm:
            print(f"   {k}: {llm[k]}")
    mem = h.get("memory") or {}
    print(f"   memory: rss {mem.get('rss_mb')} MB, peak {mem.get('peak_mb')} MB, limit {mem.get('limit_mb')} MB")


if __name__ == "__main__":
    live()
    prices()
    rates()
    usage()
    sys.exit(0)
