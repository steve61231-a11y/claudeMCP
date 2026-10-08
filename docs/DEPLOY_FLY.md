# Moving off Render — the runbook

Written the day the Render account was suspended over an unpaid $200, with the
dashboard still reachable. It assumes nothing except this repository and your
saved secrets.

The shape of the move:

| | Before | After |
|---|---|---|
| App | Render `pulse-engine`, standard plan | Fly.io, `shared-cpu-1x` 1GB |
| Search | Render `zenith-searxng`, starter plan | not deployed (optional) |
| Database | Render Postgres, **free plan, no backups** | Neon free tier, billed separately |
| Cost | ~$32/month | ~$5–6/month for the app, $0 for the database |

**Check Fly's current pricing yourself before committing.** The figure above is
the order of magnitude for an always-on 1GB shared machine, not a quote.

---

## 0. First, while the old dashboard still opens

This is the only step that cannot be done later, and the only one that is
genuinely urgent. **Everything else survives in git; the corpus does not.**

1. Render dashboard → `pulse-postgres` → **Connect** → **External Connection**.
   It ends in a real domain (`…-a.<region>-postgres.render.com`). The string in
   the service's own environment is the *internal* host (`dpg-xxxxx-a`, no
   dots) and resolves only inside Render's network.
2. GitHub → **Settings → Secrets and variables → Actions** → update
   `DATABASE_URL` with that external string.
3. GitHub → **Actions → db-backup → Run workflow**. Download the artifact.

Why through GitHub rather than `pg_dump` locally: the workflow already pins a
client new enough for the server (an earlier manual attempt failed on exactly
that — server 18.6, client 17.11), and it leaves you a dated artifact rather
than a file on one laptop.

That workflow had failed **thirty nights running** before this, silently,
because the secret held the internal hostname. It now refuses that string by
name and opens an issue when a dump does not happen.

---

## 1. Database: Neon

Free tier, and — the whole point — **billed separately from the app**. The
previous arrangement let one unpaid hosting invoice take the data with it.

1. neon.tech → new project → region closest to you.
2. Copy its connection string. It is an ordinary host, so the backup workflow
   can reach it; put it in the GitHub `DATABASE_URL` secret too, and from then
   on the nightly dump protects the new database.
3. Restore the rescued dump, if you have one:

   ```bash
   pg_restore --clean --no-owner --dbname "<NEON URL>" db-XXXXXXXX.dump
   ```

   Skip this on a clean start. The schema comes up by itself either way — the
   container runs `alembic upgrade head` before uvicorn binds.

Neon's free tier suspends an idle database and wakes it on connection, so the
first request after a quiet period pays a second or two. That is a latency
cost, not a data risk — unlike the Render free plan's 90-day deletion.

---

## 2. App: Fly

```bash
curl -L https://fly.io/install.sh | sh     # or: brew install flyctl
fly auth signup                            # or: fly auth login

cd claudeMCP
fly launch --no-deploy --copy-config       # reads fly.toml; keep the name or change it
```

Set the secrets. These are **never** in the repo and never in a chat — type
them straight into this command from your password manager:

```bash
fly secrets set \
  DATABASE_URL="postgresql://…neon.tech/…?sslmode=require" \
  ANTHROPIC_API_KEY="…" \
  PULSE_API_KEY="…" \
  ALLOWED_ORIGINS="https://<your-app>.fly.dev"
```

Optional, and the report says plainly what is missing without them:
`SOCIALCRAWL_API_KEY` (all paid social — without it the corpus is news-heavy,
which skews reports critical), `NEWSAPI_KEY`, and
`X_USERNAME`/`X_EMAIL`/`X_PASSWORD` with `ENABLE_TWSCRAPE=true`.

```bash
fly deploy
fly logs                 # watch alembic run, then uvicorn bind
fly open                 # the app
```

### Sizing, measured rather than guessed

Peak resident memory on the heaviest in-process path — the whole app imported,
the spaCy model loaded, TF-IDF clustering over 800 corpus items, then NER over
200 — came to **600 MB**. A real window is ~100–700 mentions, so that is a
ceiling and not a typical run.

```
baseline python                     9 MB
+ app imported                    147 MB
+ spaCy model loaded              229 MB
+ TF-IDF clustering, 800 items    302 MB
+ spaCy NER over 200 items        600 MB
```

So: 512 MB will OOM under load, **1 GB is the right size**, and the 2 GB the
old deployment paid for was never needed. Serving the page and `/api/health`
alone sits at ~160 MB.

If you ever set `USE_LOCAL_ML=true` (sentence-transformers), none of this
holds — that needs ≥2 GB, and the packages are deliberately not installed.

### Two settings in `fly.toml` that are not defaults

`auto_stop_machines = false` and `min_machines_running = 1`, and they are the
reason this costs anything at all.

A report takes tens of minutes and runs as a **background job** — the browser
polls, so the run itself generates no HTTP traffic. Fly's idle detector only
sees requests, so with auto-stop on it would suspend the machine in the middle
of a run that was working perfectly, and the page would show a report that
never finishes. That is the precise failure this codebase spent a week
eliminating from the pipeline; paying a few dollars a month is cheaper than
rebuilding it in the infrastructure.

---

## 3. What was dropped, and how to get it back

**SearXNG** was a second paid service at $7/month. `searxng_url` defaults to
`""` and the corpus is built from GDELT, Google News, Reddit, YouTube and
Wikipedia without it. If you want it back, run it as its own Fly app and set
`SEARXNG_URL` — but it does not belong in the critical path of a deploy you
need working today.

**Server-side PDF export.** Chromium is ~300 MB of image and the memory
headroom that forced the larger instance. "Download PDF" prints from the
reader's own browser, which has already rendered the report, so the
user-facing path is unaffected. The server endpoints remain and degrade
honestly where no browser is installed. To put it back:

```toml
[build.args]
  INSTALL_CHROMIUM = "true"
```

and raise `memory` with it.

---

## 4. Afterwards

- Point `ALLOWED_ORIGINS` at the real origin, not `*`.
- Confirm the nightly backup is green **once**, against Neon. Until you have
  seen it pass, assume there is no backup — that assumption is exactly what
  went wrong last time.
- `.github/workflows/keepalive.yml` existed to stop Render's free tier
  sleeping. With `min_machines_running = 1` it does nothing; delete it or leave
  it, it is harmless.
- The old Render blueprint (`render.yaml`) stays in the repo. It still
  describes a working deployment and is a useful reference for any host that
  takes a container, but it is no longer the one in use.
