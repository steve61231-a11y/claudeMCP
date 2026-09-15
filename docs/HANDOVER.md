# Handover — continuing this project from nothing

Written for the case where the Claude Code subscription, the Render account, or
both have lapsed, and you are starting again somewhere else.

**Assume you have exactly one thing: the GitHub repository.** That is enough,
provided you did the two things in "Before you lose access" below. If you did
not, read that section first and see what you can still rescue.

---

## 1. Start a new Claude Code session on the project

```bash
git clone https://github.com/steve61231-a11y/claudeMCP.git
cd claudeMCP
claude
```

That is the whole handover. `CLAUDE.md` at the repo root is read automatically
at session start, so the new session begins knowing what the project is, how it
is laid out, the traps that have already been paid for, and how to run it.

**A prompt to open with**, if you want to hand the session its bearings
explicitly rather than relying on the file alone:

> This is Mũũgĩ, a Kenyan political/business intelligence platform. Read
> CLAUDE.md, docs/HANDOVER.md and docs/DISASTER_RECOVERY.md before doing
> anything. Then run the test suite and tell me the current state — what works,
> what is broken, and what the open work is. Do not change anything yet.

There is no other state to restore. The conversation history is not needed:
what mattered from it is in `CLAUDE.md`, in `docs/PROJECT_HISTORY.md`, and in
the test docstrings, which describe the live failures they were written for.

## 2. Bring the app back up

The deployment is described entirely by `render.yaml`, which defines both
services. On Render: **New → Blueprint → point at this repo.** It proposes
everything.

It is a plain Docker app, so it is not Render-specific. `engine/Dockerfile.render`
builds and runs it anywhere that takes a container — Railway, Fly.io, a VPS.
The entrypoint runs `alembic upgrade head` before starting, so the schema comes
up to date by itself against whatever `DATABASE_URL` points to.

Fill in the environment variables from your password manager. `CLAUDE.md` lists
the required ones and what each does; `render.yaml` carries the rest with
comments.

## 3. Bring the data back

The database is the only part of this system that is not reproducible from the
repo. See `docs/DISASTER_RECOVERY.md` — it covers the backups, the restore, and
the standing risk.

If there is no backup and the data is gone: the app still works. It rebuilds a
corpus by running. You lose accumulated history, not capability.

---

## Before you lose access — the two things that matter

Everything else in this document assumes these are done. Neither takes long,
and neither can be done afterwards.

### Save the secrets somewhere you control

The API keys in the Render dashboard are **not in this repo** and are not
derivable from anything. Losing the dashboard without them saved elsewhere is
the one scenario that is genuinely painful — you would have to reissue every
key from every provider.

Copy these into a password manager (1Password, Bitwarden, anything):

```
DATABASE_URL          ANTHROPIC_API_KEY     PULSE_API_KEY
LLM_API_KEY           SOCIALCRAWL_API_KEY   NEWSAPI_KEY
LLM_BASE_URL          LLM_MODEL             LLM_BULK_MODEL
ALLOWED_ORIGINS       X_USERNAME/X_EMAIL/X_PASSWORD
```

Never paste them into a chat, an issue, or a commit.

### Get a database dump out

`.github/workflows/db-backup.yml` does this nightly once you set the
`DATABASE_URL` repository secret — but it runs on **GitHub**, not Render, so it
needs the database's **External** connection string, not the internal
`dpg-xxxxx-a` hostname the app uses.

To take one right now, from any machine with `psql`:

```bash
pg_dump "<EXTERNAL DATABASE_URL>" --format=custom --no-owner --no-privileges \
  --file=muugi-$(date +%Y%m%d).dump
```

Keep that file somewhere that is not Render.

---

## What state the project is actually in

**Working and tested.** ~1,155 tests, all passing. Search reports and Issue
Maps both run end to end and produce real analysis. PDF download works for
both. Citations resolve to real links. Runs are bounded and cannot hang.
Failures are reported honestly rather than rendering as empty sections.

**Known limits, not bugs.**

- Without `SOCIALCRAWL_API_KEY` there is no TikTok, Instagram, LinkedIn or X in
  the corpus. Reports then lean on news coverage, which skews critical and
  neutral — a politician can appear to have no supporters when the truth is
  that their supporters are on platforms nobody read. The report says the
  sources failed, but a reader may not connect the two.
- Reddit returns 403 from datacenter IPs whatever User-Agent is sent. Fixing it
  means Reddit's OAuth API (free, needs an app registered at
  reddit.com/prefs/apps).
- Kenyan outlets (nation.africa, standardmedia.co.ke) intermittently return 500
  to the article fetcher. Unresolved; suspected bot protection.
- Anything not on `LLM_PROVIDER=anthropic` is banner-marked test-grade. That is
  deliberate.

**Open work, roughly in order of value.**

1. Source resilience — the three items above. With the paid social tier off,
   free sources *are* the corpus, so this is the biggest quality lever.
2. Verify PDF output on a large real report: pagination across page breaks,
   whether the network graph renders in print, whether the timeline strip
   survives. Only synthetic samples have been checked.
3. A systematic pass for cross-section contradictions. Three specific ones were
   found and fixed; no exhaustive audit has been done.
4. Move the database off a free plan (see `DISASTER_RECOVERY.md`).

---

## A note on how this project has gone wrong before

Worth reading before you start changing things, because the same failure keeps
recurring in different clothes: **a fix applied to the instance rather than to
the rule.**

- A citation pattern that matched `[ref=x]`, then had to learn `(ref=x)`, then
  `(ref=x, y)` — three rounds, because each fix pinned the exact string that
  had been seen rather than the family it belonged to.
- Prose fields linkified on the issue map and not the report, because the leak
  had only been *observed* on a map, though both run the same analysts under
  the same rules.
- A deadline added to the analyst fan-out while the stages immediately after it
  were left unbounded.

Each time, the question that would have saved a round is: *what rule does this
bug violate, and where else does that rule apply?*

The tests now enforce rules rather than instances — they seed a citation into
every prose field, they grep the codebase for unbudgeted model calls. Keep that
habit; it is the difference between fixing this once and fixing it four times.
