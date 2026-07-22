---
title: Zenith Intelligence
emoji: 🏔️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Zenith Intelligence

Public-signal intelligence platform. The engine (FastAPI) scrapes news, Reddit,
YouTube, Wikipedia and X, reads every mention with an LLM, and serves a full
report, an issue-intersection map, a relationship network, and a live
mission-control dashboard. It also serves the single-file frontend at `/`, so
this Space URL is the whole app.

Runs as a Docker Space (16GB RAM) so the full pipeline runs without the
memory limits of a small instance.

## Required Space secrets (Settings → Variables and secrets)
- `ANTHROPIC_API_KEY` — for the LLM analysis (required for live reports)
- `DATABASE_URL` — a Postgres connection string (Neon)
- `LOW_MEMORY` = `false` — full-power pipeline (we have 16GB here)
- `SERVE_PRECACHE_FIRST` = `false` — run genuinely live for any subject

## Optional
- `PULSE_API_KEY` — gate the API (must also be entered in the app's Settings tab)
- `ENABLE_TWSCRAPE` = `true` + `X_USERNAME` / `X_EMAIL` / `X_PASSWORD` — live X data
- `PREWARM_NAMES` — comma-separated subjects kept fresh by the scheduler
