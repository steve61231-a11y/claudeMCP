# Mũũgĩ — working notes for whoever picks this up

Read this first. It is written for a Claude Code session (or a developer)
starting cold, with nothing but this repository.

**This repo is the only thing that survives.** Subscriptions lapse, hosting
lapses, chat sessions end. So everything needed to rebuild, redeploy and keep
building lives here, in git — not in a chat log, not in a downloaded file.

---

## What this is

A political and business intelligence platform for the Kenyan market. You give
it a name — a politician, a CEO, a ministry, a company — and it reads
everything public about them and writes an analyst's report.

Three products, one engine:

| | What it answers |
|---|---|
| **Search report** | "What is the public record on this person, and what does it mean?" |
| **Issue Map** | "How is this person connected to this institution or issue?" — e.g. `Odious Debt case × IMF` |
| **Network** | Who is connected to whom, from the accumulated corpus |

It is intended to be **sold**. That matters for several decisions below,
particularly the test-grade banner.

## The one rule this codebase is built around

> **A section that failed must never look like a section with nothing to say.**

Almost every bug worth fixing here has been a version of that. An empty report
because the model was down looks identical to an empty report about a quiet
subject — unless the code goes out of its way to tell them apart. Hence the
stage ledger (`engine/stages.py`), the run-health accounting
(`engine/health.py`), the "derived, not analysed" banners, and the honesty
about sources that failed.

If you change something here, keep that rule. It is the product.

---

## Layout

```
engine/
  api_server.py     FastAPI app + every HTTP endpoint. Also SERVES THE FRONTEND.
  pipeline.py       run_analysis() — the whole Search report, stage by stage.
  llm.py            Every model call. Budgets, retries, circuit breaker, costs.
  health.py         Did the model actually answer? Preflight + run health.
  stages.py         The ledger: what ran, what failed, what was empty.
  config.py         Every setting, with its default and why.
  reports/          Analysts, digest, issue map, citations, PDF export.
  ingestion/        Connectors (GDELT, Google News, Reddit, YouTube, X…) + http.py
  agents/           Resolution, verification, credibility, knowledge graph.
  intelligence/     Narratives, embeddings, people graph, simulator.
  processing/       Sentiment, entities.
  admin/purge.py    Deleting accumulated run data, with rails.
  alembic/          Migrations. Run automatically on deploy.
  tests/            109 files, ~1155 tests. They are the specification.
web/pulse_app.html  The ENTIRE frontend. One file, ~3,400 lines, no build step.
railway.json        Deploy config — Railway. THIS is the live one.
                    docs/DEPLOY.md is the click-only guide; no terminal needed.
fly.toml            Deploy config — Fly.io. The CLI alternative, cheaper.
render.yaml         The old Render blueprint. Kept as a reference; not in use.
docs/               Recovery runbook, data policy, this project's history.
```

**The frontend is one HTML file served by the backend.** No npm, no bundler,
no `frontend/` build (that directory is legacy). Edit `web/pulse_app.html`
directly. `window.ZENITH = {...}` at the bottom exports the render functions,
which is how the PDF exporter reuses them.

## Running it

```bash
pip install -r engine/requirements.txt
python -m spacy download en_core_web_sm
python -m pytest engine/tests -q            # needs Postgres; see below
uvicorn engine.api_server:app --reload      # then open http://localhost:8000
```

Tests need a Postgres at `TEST_DATABASE_URL` (default
`postgresql+psycopg2://postgres:postgres@localhost:5432/political_intel_test`).
The fixture runs real migrations and drops the schema on teardown, so if a run
is interrupted the next one fails with a hundred errors that are not your
fault. Reset it:

```bash
psql -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
```

---

## Things that will bite you

These are all paid for in production failures. Each has tests pinning it.

**The corpus is meant to compound, so the queries have to.**
`ingestion/learning.py` reads back what every query returned on this
subject's previous runs — `IngestionTask.result_count` is NEW,
non-duplicate mentions, because the store is ON CONFLICT DO NOTHING on a
content hash — and the next run repeats what found material, sets aside
what came back empty twice, and reserves a fixed share of every budget for
questions never asked. Three rules that are easy to break: a FAILED task is
not an empty result (a source being down is not a question being answered);
retirement decays, because a subject with no court record this year may
have one next year; and the exploit share is capped, because a loop that
only repeats what worked stops discovering. It ranks on mentions
entity linking tied to THIS subject (`RawMention.task_id` → `MentionEntity`),
not on row count: a query returning 120 rows nobody can connect to the
subject would otherwise outscore one returning 6 that are about them, and
the loop would learn that noise is the best strategy while looking from
outside as though it worked. Where the attribution is missing — rows stored
before `task_id` existed — it falls back to novelty and `ranked_by` says so.

**A style rule applied past the sense it was written for fires on correct
prose.** `docs/economist-style-guide.md` holds the guide's prescriptive
sections and `reports/style_check.py` implements them — but six of its own
pairs are deliberately not applied, because the replacement is wrong in the
collocations this product writes: "health facility" is the standard Kenyan
term, a UK peer is a person, firms exit markets, nobody "breaks" human
rights, "the following three issues" is correct English, and "the record
does not establish X" is the sentence this engine's OWN house style mandates
for thin evidence. `stakeholder` and `governance` are on the horrible-words
list and are also the name of a section and a framework here. Each exclusion
is pinned by a test, because a check that fires on good writing is one its
reader learns to skip.

**Never state a cause you have not checked.** This has cost an evening three
times: an operator sent to a Railway "Networking" menu that cannot exist for
a crashed service; a nightly backup diagnosed as holding the wrong hostname
when the real cause was a TLS refusal; and the grounding banner advising
`LOW_MEMORY=false` to an operator who had already set it, because the page
guessed the common cause rather than being told the real one. Where a check
degrades, the code that knows WHY must say so — `grounding.check` returns
`method_reason`, and `/api/health` reports `analysis` so a deployment's real
capability can be confirmed without generating a report.

**One setting must not govern two unrelated costs.** `low_memory` protected
RAM on a 512MB instance *and* set the width of every model fan-out in the
product — two workers on the corpus digest instead of six, three analysts
instead of eight — and it is on by default. Those threads are blocked on an
HTTP response: six in flight hold about 200KB. They bought no memory and
they cost minutes. Fan-out width is `LLM_FANOUT_WORKERS` and
`INGESTION_WORKERS`; `low_memory` still governs the things that genuinely
allocate (NER, YouTube extraction, the per-mention work cap).

**A per-stage limit does not bound a run whose stages overlap.**
`llm.concurrency()` clamps each pool separately, so two overlapping stages
each got the full ceiling. Since providers reserve credit for the whole time
a request is in flight, that is a spending limit that silently multiplies —
and it is the number an operator sets when the bill arrives.
`LLM_MAX_CONCURRENCY` is enforced by one process-wide semaphore in
`llm.call_json`, held around the send and nowhere else, so a stage that fans
out cannot deadlock against its own children.

**Model budgets are shared with thinking.** A reasoning model spends its token
allowance thinking *before* it answers, so a budget sized for the JSON comes
back empty. Always size budgets through `llm.budget_for()`, never a bare
number — there is a test that greps the whole codebase for bare `max_tokens=`.

**`max_tokens` is money, not just a ceiling.** Providers reserve credit for the
whole time a request is in flight. Raising it multiplies what a parallel
fan-out ties up, which is how a working account started returning HTTP 402.

**Not every failure is the provider's fault.** A truncated or unparseable reply
means the provider *served* us. Counting those toward the circuit breaker is
how one under-budgeted call took down an entire run. See
`engine/tests/test_breaker_blames_the_right_thing.py`.

**Every stage needs a deadline.** `try/except` does not bound a call that never
returns — there is no exception to catch. The analyst fan-out, the tail after
it, and the whole run each have a ceiling now. Without one, a report hangs
forever at "12/15 sections".

**A commit expires every instance, so a later attribute read is a hidden
SELECT.** `SessionLocal` sets `expire_on_commit=False` for that reason. A
400-second run died at the end because reading `ingestion_run.stats` — a
value already in memory — re-SELECTed the row on a pooled connection that
had been idle through the whole model phase, and Neon had closed it.
`pool_pre_ping` cannot help: it validates at CHECKOUT, and an open
transaction holds one connection for the duration. The test fixture uses the
same setting, because a fixture whose sessions behave differently from
production cannot catch the bug that difference causes.

**A run that fails late must still hand over what it finished.** Sections
are saved as they land, so a run that dies at the end has most of a report
in the store; `_partial_payload` delivers it under a banner that says it is
incomplete. The founding rule at the scale of a whole run — and the
difference between seven minutes wasted and seven minutes spent.

**Roll back after a failed flush.** SQLAlchemy will not let a session continue
after one; every later statement raises `PendingRollbackError` naming the
*original* error, so the stage that reports the failure is never the stage that
caused it. Guards around DB work must call `_rollback_quietly(db)` first.

**Models do not commit to one citation format.** They write `[ref=x]`,
`(ref=x)`, `ref: x`, `(ref=x, y)` interchangeably. `reports/citations.py`
matches the family. When you add an analyst that writes prose, add its fields
to `linkify_analysis` / `linkify_report` — there are tests that seed a ref into
*every* prose field and fail if one leaks.

**Free sources must be paced.** GDELT meters ~1 request / 5s per IP, and the
whole service is one IP. `ingestion/http.py` holds a per-host gap. Without it
the news backbone returns nothing and the corpus falls back to YouTube titles.

**"article" is not a table.** GDELT stores news as `RawMention` rows whose
`source_type` is `"article"`, same as real `Document` rows. Corpus builders
carry an explicit `kind` field. Never infer which table an id belongs to.

---

## Configuration

Every setting lives in `engine/config.py` with a comment explaining its value.
The ones you cannot run without:

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Postgres — Neon, billed separately from the app on purpose. See `docs/DEPLOY_FLY.md`. |
| `PULSE_API_KEY` | Gates the API. The frontend sends it as `x-api-key`. |
| `ALLOWED_ORIGINS` | CORS. |
| `ANTHROPIC_API_KEY` | Only when `LLM_PROVIDER=anthropic`. |

Worth setting on anything larger than a 512MB instance:

| Variable | Notes |
|---|---|
| `LOW_MEMORY=false` | Confirm it took effect at `/api/health` → `analysis`, which reports the parsed setting, whether the entity model loads, and which extractor grounding will use. Turns spaCy NER back on — the grounding check falls back to a capitalised-name pattern without it — and lifts the per-mention work cap from 140 to 300. Needs ~1GB; see `docs/DEPLOY_FLY.md` for the measurements. |
| `LLM_FANOUT_WORKERS` | How wide a parallel stage of model calls runs. Default 8. Raising it is latency, not memory. |
| `INGESTION_WORKERS` | The same for collectors. Default 8. |
| `LLM_MAX_CONCURRENCY` | A hard ceiling on model calls in flight across the whole process. Unset means none, which is what the paid Anthropic path wants; set it on a metered or rate-limited backend. |

Model backend — either Anthropic directly, or anything OpenAI-compatible:

```
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=...
LLM_MODEL=...            # reasoning: analysts, verdicts
LLM_BULK_MODEL=...       # high-volume: digest, sentiment, entities. Set this —
                         # unset, it falls back to LLM_MODEL and costs several
                         # times more for mechanical extraction.
LLM_PRICE_IN=0.75        # per million tokens; drives the real spend figure
LLM_PRICE_OUT=3.75       # in Mission Control. Set them to your model's rates.
```

**Only `LLM_PROVIDER=anthropic` produces production-grade reports.** Everything
else is stamped "Test-grade — not for a client" in the UI, deliberately, because
the prompts and anti-hallucination guarantees are tuned against that model. If
you are selling a report, run it on Anthropic.

Optional sources: `SOCIALCRAWL_API_KEY` (TikTok/Instagram/LinkedIn/X — the
paid social backbone; without it there is no social media in the corpus at all,
which skews reports toward news coverage and away from supportive voices),
`NEWSAPI_KEY`, `SEARXNG_URL`, `X_USERNAME`/`X_EMAIL`/`X_PASSWORD`.

---

## Where to look when something is wrong

- **Just deployed and nothing works** → `/api/health` → `schema`. `start.sh`
  starts the server even when `alembic upgrade head` fails, on purpose, so a
  failed migration leaves the app running against an older schema than its
  models declare — and the first symptom is every mention INSERT failing on a
  missing column, which reads as "collection returned nothing".
- **The report is empty** → `/api/admin/model-check`, then the run-health
  banner on the page. It distinguishes "model down" from "nothing found".
- **A section is missing** → the stage ledger is on the page under "Some
  sections could not be produced", with the actual exception.
- **The run never finishes** → it now cannot exceed `REPORT_DEADLINE_SECONDS`.
  If it does, something new is unbounded.
- **Costs** → Mission Control shows real tokens and dollars, priced at the
  configured backend's rates.
- **A stuck subject** → a dead run is reaped after 30 minutes of silence; press
  Generate again.

## Working style that has served this project

The tests are the specification and the memory. Almost every test file opens
with a docstring describing the *live failure* it pins — read those before
changing behaviour, because they explain why something is the way it is far
better than the code does.

When a bug appears, fix the category, not the instance. This project has
repeatedly paid for the opposite: a citation pattern taught one more spelling
each time, prose fields fixed one page at a time. If you find a bug in one
place, ask what rule it violates and where else that rule applies.
