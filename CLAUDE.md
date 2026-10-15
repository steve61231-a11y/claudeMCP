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
  ingestion/        Connectors (GDELT, Google News, Reddit, YouTube, X,
                    Kenya Law + Kenya Gazette…) + http.py
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

**A deploy that never happened looks exactly like a deploy that happened.**
Railway deployed by watching this repository through its GitHub App. That
connection broke on 9 October and said so in one grey line on one settings
page — *"Auto deploy unavailable"*, beside a repository and branch that were
both correct. The site stayed up serving that day's code while twenty-three
commits landed on the branch and none of them ran. The stale code was not
the cost; the cost was that every fix pushed in those three days was tested
against a container which did not contain it, so each appeared not to work
and the next diagnosis started from a false fact. The founding rule at the
scale of the whole deployment. So: `/api/health` reports `build_detail` —
which commit is *running*, however it has to work that out — and this is the
first thing to check when a fix appears not to work, before touching the
fix. `.github/workflows/deploy.yml` pushes the code to the host instead of
waiting to be pulled, and verifies the commit against the live container
rather than trusting the deploying tool's exit code.
`.github/workflows/deploy-drift.yml` catches the one case a deploy workflow
structurally cannot: the deploy that was never triggered at all.

**A guard wired to a module nothing serves is worse than no guard.**
`settings.validate_for_startup()` exists to refuse a container with no model
credential. It is registered on `engine/main.py`'s app and the container
runs `engine.api_server:app`, so it has never executed in a deployment — and
it reads, to anyone who finds it, as proof the case is handled. A deployment
therefore ran for days with no key and said nothing until a report died
against someone else's 401. `/api/health` now reports `llm` — the backend,
whether the credential it needs is PRESENT (never any part of its value;
the endpoint is public), and whether a report from this deployment would be
production-grade. `configuration_problems()` is the non-raising half, because
a startup check wants to refuse and a health endpoint wants to report.

**Explain a failure with what you know, not only with what a stranger said.**
A run died on OpenRouter's "Missing Authentication header" and the page told
the operator their key had been rejected. It had not: no key existed, the
provider never received one, and the engine could have said so from its own
configuration. A third party has no opinion about a variable it was never
shown, so it can only describe the symptom — where a credential is absent
that is the finding, and the 401 is a consequence of it. `llm` owns the
question (`missing_credentials()`), because it owns the backend and builds
the Authorization header, and it is the seam a test replaces when it stands
in for the model. The explanation is gated on the error being
authentication-shaped: a missing key does not explain a 404 for a retired
model, and letting it try replaces a correct diagnosis with a plausible one,
which is worse than none because it reads as an answer.

**The analyst may not introduce identity the sources never raised.** A live
report on a Kenyan politician came back referring to Jews. Nothing in the
corpus did — the model reached for a conspiracy frame that exists in its
training data and not in the evidence, and wrote it into a document meant
for a paying client. Every other failure here costs time; this one is
defamatory and one occurrence ends the product. The fix is NOT a forbidden
word list: Kenyan political reporting legitimately concerns ethnic
coalitions and religious communities, and a report that cannot say "Luo"
cannot describe Kenyan politics. It is `grounding.py`'s rule pointed at
identity — `reports/protected_terms.py` removes a sentence whose ethnic,
religious, racial or other protected term appears nowhere in the corpus, and
leaves untouched every term the sources themselves used. It is the one guard
here that rewrites rather than reports, because a sentence blaming a
religion is not improved by a footnote; the founding rule is kept by
declaring every removal with its sentence and its trigger. Both products run
it. With no corpus it refuses to judge rather than cut everything.

**A number must not be reported twice under two names.** `strength_score` is
mentions plus engagement×0.01, and engagement is zero across a news corpus —
so it IS the mention count. `growth_rate` divided by `max(first_half, 1)`,
so a storyline that began inside the window came out as a rate equal to its
own mention count. Handed all three, a model wrote "(69 mentions, strength
69, strong growth 69%)" into an executive brief: one number wearing three
hats, each read by a client as separate corroboration. Both now carry a
basis (`strength_basis`, `growth_basis`) and are withheld rather than
zeroed where they measure nothing — a zero reads as "flat", which is a
measurement nobody made. Same rule as `actions.py`'s `movement_basis`.

**`share()` returns markup, so a slot that escapes shows it.** A storyline
card rendered `<span title="69 of 699 mentions">10%</span> of mentions` to
the reader, because `deepCard` escapes its subtitle. `sharePlain()` is the
twin for text slots; it inlines the counts instead of hiding them in a
tooltip nobody can hover in a card subtitle.

**A press summary is not intelligence.** A live report's biggest storyline
was "Ruto board appointments" — sixty-nine news items about appointments to
EPRA, LAPSSET and the TSC — and the corpus held not one Gazette notice. It
also carried the headline "High Court rules presidential advisory offices
and appointments unconstitutional" and not the judgment. That is what "this
report is too short" meant: not volume, kind. More queries against four
keyword sources return more coverage OF events; the documents those events
are reported FROM were absent by construction, and no budget on a news
connector reaches them. `ingestion/kenya_law_connector.py` reads the two
that are free and keyless — the Kenya Gazette (appointments, revocations,
commissions) and Kenya Law case law. They carry `source_type` `gazette` and
`judgment`, `raw_payload["record"] = "primary"`, and the long mention budget,
because truncating a gazette notice to a tweet's budget discards the part a
client is buying. They get their OWN probes: DISCOVERY_PROBES asks what the
press said, those words appear in no judgment, and the learning loop would
retire a source that had been asked the wrong question rather than being
wrong. Written without network access to kenyalaw.org, so the parser is
structural (anchors and their text) rather than selector-based, and the one
hard requirement is the founding rule: a refused host, a page that parsed to
zero rows, and a subject with genuinely no record are three different
findings — "this person has no court record" is a publishable claim and must
never be produced by a connection error.

**Engagement ranks popularity; this product sells evidence.**
"Representative mentions" sorted by views plus likes×5, and the state does
not post for engagement — so a Gazette notice scores zero, sorted last, and
was cut from the top ten every time. The primary record was collected,
stored, and then structurally excluded from the one section a client opens
to check the work. Not fixed by inventing a score for it, which would be
the kind of fabricated number this codebase removes: half the sample is
RESERVED for `record == "primary"`, the way `learning.py` reserves a share
of every budget for questions never asked, and capped at half so it cannot
flood the sample either. The page marks it — `.mentr.is-primary` and a
badge naming the kind — because the whole argument for collecting the
state's own publications is that a reader can tell "a newspaper reported an
appointment" from "here is the notice that made it", and that argument is
worth nothing if the two render identically.

**An undated item is not a busy day.** Eleven connectors fall back to
`window_end` when a date will not parse. That is the right fallback — it
keeps ordering and windowing working — and it stamps every undated item
onto the same day, so the volume chart counted the pile as real and a live
report drew "peak 380 mentions in a day" over a 699-item corpus, a cliff on
the last day of every report presented as its most striking number. The
founding rule again: "we could not date this" had become "this happened on
15 October". Connectors now set `raw_payload["date_estimated"]`, the flag
travels explicitly through `_mention_dicts` (raw_payload is not carried
there and has no other reader), and `by_day` counts only items whose date
is known. With nothing datable there is NO peak — `peak.date` is None, and
the page checks it rather than the truthiness of the object around it,
which printed "peaked —". The reading also carries `dated_mentions` and
`undated_mentions`, because a peak resting on a tenth of the corpus is a
different claim from one resting on all of it.

**The grounding banner told an operator not to send a clean report.**
A live run ended "15 names in this report do not appear in any source", and
not one was an invented name: five mention UUIDs, `HTTPError`, our own
`SocialCrawl`, sentence-case nouns like `Videos`, a cut heading, and
`Kenya Kwanza's` — the governing coalition, which tokenised with the
possessive attached and so could not match the "Kenya Kwanza" the sources
say constantly. A UUID yields the four-letter token `abce`, long enough to
count as distinctive and present in no corpus ever written. The
capitalised-pair fallback could never produce any of them; the ENTITY MODEL
did, and it runs whenever LOW_MEMORY is false — so the guard has to sit
after both extractors. `is_checkable()` rejects machine identifiers, code
identifiers and fragments outright and gives the reason; `is_accusable()`
is the asymmetry that keeps the check thorough: a single word is still
CHECKED, because "Treasury" and "Parliament" are real and dropping them
would make the entity model no better than the pattern it is recommended
over, but a single word the corpus lacks is reported as unverified rather
than as an invention, because it looks identical to sentence case. Possessives
are stripped before matching. The founding rule pointed at this file: "we
could not check this" must never render as "this was invented".

**A section whose finding is its subtitle is not empty.** "Sentiment
coverage" says "459 of 459 mentions read by the analyst" in its heading and
appends a body only when something is WRONG, so the empty-section note
labelled a working section as failed — the forbidden failure pointed
backwards. `labelSections` now reads `.sect-sub` before deciding, and
removes a stale note if one was added.

**An exhausted account is configuration, not a failed stage.** A live
report showed "Some sections could not be produced — transcripts: stopped
after 3 consecutive failures … HTTP 402 INSUFFICIENT_CREDITS", and the
operator went looking for a bug that did not exist. Nothing was broken:
`SOCIALCRAWL_API_KEY` is set, the account behind it has no credits, and the
stage is gated on the variable EXISTING rather than on the account working.
The message also never named SocialCrawl — the `youtube/<id>` in it is the
video being asked about, not the service being called, which is a trap for
anyone reading it, including the author of this file. It now names the
service, the variable, both remedies (top up, or remove the variable to turn
the stage off cleanly) and the fact that nothing else in the report depends
on it, which is the operator's real question. A genuine 400 still reports as
a genuine 400, so this cannot swallow a real fault.

**One excuse over every missing section is a guess wearing a card.**
A finished run listed "Sentiment over time" and "Sentiment Framework V1.0"
under one sentence: "a first report has no earlier one to compare against …
Waiting will not add them; another run might." True for the first. False
for the second — Sentiment Framework is gated on
`out_of_time("sentiment_framework")`, so it had been SKIPPED FOR TIME, the
ledger said exactly that in words, and `finishedSections` threw the ledger
away. The founding rule broken twice in one card: a section skipped for
time made to look like one with nothing to say, and the remedy stated
backwards for the one section another run probably WOULD produce. The card
now reads `sectionStatus` and gives each section its own reason — ran out
of time (retry), the stage failed (with the error), needs an earlier report
(`COMPARISON_SECTIONS`, named not inferred, because telling someone to
re-run for something that needs a previous run is a loop with no exit),
nothing in the corpus supported it, or honestly "no reason was recorded".

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

**A compounding corpus makes a CPU stage slower every run, and a deadline
cannot save it.** A live run read 819 new mentions and then sat for an hour
against a 1800s ceiling. Two different kinds of unbounded. First,
`out_of_time()` existed and was called at three points, ALL of them after
the long middle — the ceiling was present and never asked, so narratives and
the network ran unwatched; both are guarded now and degrade (empty set,
empty snapshot) rather than skipping the report. Second, `build_narratives`
embeds every mention it is handed and runs HDBSCAN over the result: both are
CPU-bound, superlinear, and uninterruptible, so a clock would only notice
afterwards. The INPUT is what has to be bounded — `narrative_corpus_cap`,
newest first, because a report about the last month should cluster the last
month — and the cap is disclosed in `narrative_corpus` when it bites and
silent when it does not, because a note on every report is noise.

**Every stage needs a deadline.** `try/except` does not bound a call that never
returns — there is no exception to catch. The analyst fan-out, the tail after
it, and the whole run each have a ceiling now. Without one, a report hangs
forever at "12/15 sections".

**No session may hold a connection across a phase that does not use it.**
The two long phases — scraping and the analyst fan-out — take minutes and do
no database work, and both were entered with a read transaction open from
the query just before. That connection sat idle, Neon closed it, and the
first statement afterwards died naming a SELECT nobody wrote. `pool_pre_ping`
and `pool_recycle` cannot help: both act at CHECKOUT, and a held connection
is never checked out again — they were correctly configured and looking the
other way, which is worse than their absence because they read as proof the
problem was handled. `db.session.release_connection(db)` is called at both
seams and `test_no_connection_is_held_across_a_long_phase.py` asserts the
session's transaction state at the moment each phase starts.

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

Free primary-record sources, on by default and needing no key:
`ENABLE_KENYA_GAZETTE`, `ENABLE_KENYA_LAW` (both kenyalaw.org, paced at 2s).
Turn them off only to make a run faster; they are the most citable material
in the corpus.

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
