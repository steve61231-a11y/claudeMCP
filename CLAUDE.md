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
render.yaml         Deploy blueprint. Both services. Read it before deploying.
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
| `DATABASE_URL` | Postgres. See the warning in `docs/DISASTER_RECOVERY.md`. |
| `PULSE_API_KEY` | Gates the API. The frontend sends it as `x-api-key`. |
| `ALLOWED_ORIGINS` | CORS. |
| `ANTHROPIC_API_KEY` | Only when `LLM_PROVIDER=anthropic`. |

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
