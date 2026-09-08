# Disaster recovery — staying safe if Render or Claude Code lapses

Two things could go away: the **Render subscription** (hosting) and the
**Claude Code subscription** (the assistant building this). Neither one, on
its own, should be able to take the product down or lock you out of it.
This document is the plan and the checklist for making that actually true.

## What's already safe, and why

- **All source code** — `steve61231-a11y/claudeMCP` on GitHub. This has
  nothing to do with Render or Claude Code; it is your repository, on your
  GitHub account, and it stays there regardless of either subscription.
  Anyone (you, another developer, a different AI assistant) can clone it and
  keep working the moment they have git access.
- **The database is NOT safe.** It is `pulse-postgres`, defined at the top of
  `render.yaml` on Render's **free** Postgres plan, and the live
  `DATABASE_URL` confirms it (`pulse@dpg-...-a/political_intel`). Two
  compounding risks:
    1. **Free Render Postgres is deleted 90 days after creation.** This is a
       hard deadline, not a warning.
    2. **The free plan has no automated backups.** If the account is
       suspended for non-payment, or the 90 days elapse, the data is gone
       with nothing to restore from.

  An earlier version of this document claimed the database had been moved to
  a managed Neon Postgres, on the strength of a stale comment in
  `render.yaml`. That was wrong. The database is on Render, on the free
  plan, and it is the single point of failure for the entire product.

  **The one change that actually removes this risk** is moving the database
  off the free plan — either to Render's paid Postgres (which adds automated
  daily backups) or to a separate provider such as Neon or Supabase, whose
  billing is independent of Render's, so a Render lapse cannot take the data
  with it. Until that happens, the nightly dump below is the whole safety
  net.
- **The deployment recipe** — `render.yaml` at the repo root is a Render
  "Blueprint": one file that describes both services (`pulse-engine`,
  `zenith-searxng`) well enough to recreate the whole deployment from
  scratch on a *new* Render account, or read as a spec for deploying
  anywhere else (Railway, Fly.io, a VPS with Docker) since the two services
  are just Docker containers.

## What was NOT already safe, and what this change fixes

1. **A day-to-day database backup that lives outside Render, and outside
   Neon's own retention window, that gets made automatically with no
   human and no Claude Code session involved.**
   Added: `.github/workflows/db-backup.yml` — a scheduled GitHub Action,
   independent of Render and of any Claude Code session, that
   `pg_dump`s the database once a day and stores it as a downloadable
   GitHub Actions artifact (90-day retention).

   **One manual step required from you** (I can't do this — it needs a
   real secret, which must never be pasted into chat):
   Go to the repo on GitHub → **Settings → Secrets and variables →
   Actions → New repository secret** → name it `DATABASE_URL` → paste the
   database's **External** connection string. Get it from the Render
   dashboard → the `pulse-postgres` database → **Connect** → **External
   Connection**. It must be the external one (hostname ending
   `.<region>-postgres.render.com`) — the value in the pulse-engine
   service's environment uses an internal hostname (`dpg-xxxxx-a`) that
   only resolves inside Render's network, so GitHub cannot reach it.
   Once that's set, the workflow runs nightly on its own; you can also
   trigger it manually any time from the **Actions** tab → **db-backup**
   → **Run workflow**.

   To restore from a downloaded `.dump` file later:
   `pg_restore --clean --no-owner --dbname "$DATABASE_URL" db-XXXXXXXX.dump`

2. **A single place listing every secret/env var the app needs, so a full
   redeploy doesn't depend on remembering what's in the Render dashboard.**
   See the checklist below. **Action for you:** save the actual values
   (not the names — the names are already in `render.yaml`) in a password
   manager (1Password, Bitwarden, etc.), not in this repo and not in chat.
   Losing access to the Render dashboard without this saved elsewhere is
   the one scenario that would genuinely be hard to recover from, because
   some of these (API keys) aren't derivable from anything else.

3. **A written, step-by-step redeploy procedure**, so recreating the whole
   thing doesn't depend on remembering how Render was originally set up
   (or on Claude Code being available to reconstruct it). See below.

## Full redeploy procedure (new Render account, or after this one is lost)

1. Fork or keep using `steve61231-a11y/claudeMCP` on GitHub — this is the
   only irreplaceable asset short of your saved secrets and DB backups.
2. In Render: **New → Blueprint**, point it at this repo. Render reads
   `render.yaml` and proposes both services (`pulse-engine`,
   `zenith-searxng`) automatically.
3. Fill in the env vars marked `sync: false` in `render.yaml` from your
   password manager (see checklist below). Everything else is either
   hardcoded in `render.yaml` or auto-generated (`SEARXNG_SECRET`).
4. Set `DATABASE_URL`. If the original database still exists, use its
   connection string. If it does not (deleted at 90 days, or lost with a
   suspended account), create a fresh Postgres — on a **paid** plan this
   time, or on a provider whose billing is independent of Render — and
   restore the newest `db-backup` artifact into it (step 6).
5. Deploy. `engine/Dockerfile.render`'s entrypoint runs
   `alembic upgrade head` before starting the server, so the schema is
   brought up to date automatically against whatever `DATABASE_URL` points
   to — including a freshly restored backup.
6. Restoring from a `db-backup` artifact: download the newest one from the
   repo's **Actions** tab → **db-backup** → most recent run → Artifacts.
   Create a fresh Postgres database anywhere, then:
   `pg_restore --clean --no-owner --dbname "$DATABASE_URL" db-XXXXXXXX.dump`
   Point the service's `DATABASE_URL` at that database.

### Env var checklist (values live in your password manager, not here)

Required for the app to run at all: `DATABASE_URL`, `ANTHROPIC_API_KEY`,
`PULSE_API_KEY`, `ALLOWED_ORIGINS`.

Needed for full functionality: `SOCIALCRAWL_API_KEY`, `NEWSAPI_KEY`,
`X_USERNAME`/`X_EMAIL`/`X_PASSWORD` (free X scraping),
`LLM_PROVIDER`/`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_BULK_MODEL`
(only if running a non-Anthropic backend for testing).

Everything else in `render.yaml` has a working default and doesn't need a
secret.

## What if the Claude Code subscription lapses specifically

Nothing here depends on it continuing to run. The GitHub Action above keeps
producing backups with zero Claude Code involvement. The code, the
`render.yaml` blueprint, and this document are all plain files in the repo
— any developer, or a fresh Claude Code session started later, or a
different coding assistant entirely, can pick the project back up from
exactly where it stands by reading this file and the code itself.
