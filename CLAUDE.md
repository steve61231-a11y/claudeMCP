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
| **Network** | *Parked 22 Oct 2026.* Returns when the fact store can say who is connected to whom from evidence, not co-mentions |

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

**The plan in force is `docs/FOUNDATION.md`** (22 Oct 2026): what was
measured to be wrong, why the reports got worse as features were added, and
the phased rebuild around a verified fact store. Read it before adding a
feature — the founder's standing instruction is a strong foundation before
more breadth, and no room for hallucination.

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
  reports/          Analysts, digest, issue map, citations, PDF export,
                    quality.py (every report measured).
  ingestion/        Connectors (GDELT, Google News, Reddit, YouTube, X,
                    Kenya Law + Kenya Gazette…) + http.py
  agents/           Resolution, verification, credibility, knowledge graph.
  intelligence/     Narratives and embeddings, influence, alerts.
  processing/       Sentiment, entities.
  admin/purge.py    Deleting accumulated run data, with rails.
  alembic/          Migrations. Run automatically on deploy.
  tests/            162 files, ~1850 tests. They are the specification.
web/pulse_app.html  The ENTIRE frontend. One file, ~6,100 lines, no build step.
railway.json        Deploy config — Railway. THIS is the live one.
                    docs/DEPLOY.md is the click-only guide; no terminal needed.
fly.toml            Deploy config — Fly.io. The CLI alternative, cheaper.
render.yaml         The old Render blueprint. Kept as a reference; not in use.
docs/               Recovery runbook, data policy, this project's history.
docker-compose.yml  Local: the same image as the host, plus Postgres and SearXNG.
```

**The frontend is one HTML file served by the backend.** No npm, no bundler,
no build step. Edit `web/pulse_app.html` directly. (A legacy `frontend/`
app and a NestJS `api/` gateway lived here until 22 Oct 2026; nothing
deployed them, and they are in git history.) `window.ZENITH = {...}` at the bottom exports the render functions,
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
structurally cannot: the deploy that was never triggered at all. A key is a
single point of failure too: the `RAILWAY_TOKEN` deployed every push from 14
to 20 October, then Railway paused deploys ("Limited Access", its treatment
of Free/Hobby plans when busy) and hours later rejected the key. So
`deploy.yml` now waits first for Railway's own GitHub connection (no key),
uses the key only if that did not land the commit, and opens an issue in
words the founder can act on when neither did.

**A guard wired to a module nothing serves is worse than no guard.**
`settings.validate_for_startup()` exists to refuse a container with no model
credential. It was registered on `engine/main.py`'s app while the container
ran `engine.api_server:app`, so it never executed in a deployment — and it
read, to anyone who found it, as proof the case was handled. (`engine/main.py`
and the gateway it served were deleted on 22 Oct 2026; `validate_for_startup`
stays only as the raising form for scripts and tests.) A deployment
therefore ran for days with no key and said nothing until a report died
against someone else's 401. `/api/health` now reports `llm` — the backend,
whether the credential it needs is PRESENT (never any part of its value;
the endpoint is public), and whether a report from this deployment would be
production-grade. `configuration_problems()` is the non-raising half, because
a startup check wants to refuse and a health endpoint wants to report. It
used to list `NEO4J_PASSWORD` and `INTERNAL_API_TOKEN` as missing on every
deployment — variables of the legacy `engine/main.py` stack, which nothing
served — so the one honest list of what is wrong opened with two false
alarms. It lists only what the served app needs now, and a variable left
over from a removed feature is ignored rather than stopping the engine.

**Measured, not believed.** Every report carries `quality`
(`reports/quality.py`): how much of the news was read in full rather than as
headlines, how many analyst sentences carry a source or sit beside verified
quotes, how many citations point at a ref the corpus does not hold (an
invented source, so not counted as one), how many figures match no number
the engine computed and none in the sources, and what the name and identity
guards removed. It is stamped with the commit that wrote the report
(`engine/build.py`, also what `/api/health` reads), shown to operators as a
"Quality check" card, and listed at `/api/admin/quality`. A change is better
when these numbers say so on the same subjects — not when one report looks
better by eye, which is how every change was judged before.

**A fact rests on words its source printed.** The verified fact store
(`evidence/facts.py`, tables `facts` and `fact_extractions`) reads each
source once, in full (`FACT_ITEM_CHARS`, not the 1,800-character lede the
extractor used to see), and keeps a record only if its quote is found in
that source contiguously after folding case, spacing and quote marks — no
fuzzy match, no word-run fallback, because a fact rests on it. A record that
fails is not stored and is counted as `dropped_unverified`, so invention
shows up as a rate. An allegation stays a claim. A source the model failed
on is `status = failed` and retried; one read with nothing in it is `read`,
and never sent again. It reads this period's unread sources alongside the analysts (model calls in
a thread that touches no database; planned and stored on the report's own
thread), the back office reads whatever it did not finish, and it is
inspected at `/api/admin/facts?name=…`. "On the record", the first section
of the Case, is built from it with no model writing anything. It is the
SUBJECT's record, not the article's: a source mentioning them in passing
yields facts about other people, so `record_for` keeps only statements that
name the subject or an alias (as whole words — "Karua" in "Karua told the
rally", not "Martha" inside "Marthas") and counts the rest as
`facts_about_others`. The extractor is told to write the name, never "he".

**A sentence names the fact it rests on, and the fact must say it.**
`reports/from_facts.py` hands a writer a numbered ledger of findings from
the record and requires each sentence to list the ones it uses; then
`check_sentence` decides, with no model: the findings exist, the
sentence's content words come from them (stems, ≥60%, and at least two),
every figure and year is in them, every multi-word name is in them or is
the subject, and anything ALLEGED or DISPUTED is attributed. The first two
of those exist because a sentence with no content — "Wanjiru will win in
2027." — passes every ratio by being empty. Removals are kept with their
reasons (`brief_check`), and the brief falls back to the analysts' when
the record is thin, saying why. Risks and opportunities come from the same
call: a checked finding plus an `assessment` the page labels as judgement —
`check_assessment` lets it reason but not introduce a name, figure or date
the cited findings lack. Items carry `issue`/`detail` (plain, for the
framework and action list), `cited`/`citations` (linked, for the page) and
`basis: verified_facts`; `quality` counts the cited sentence once.

**The insight is in the shape of the record, so the shape is counted.**
What an analyst earns by cross-reading — the story in every outlet that
traces to one original report, the claim outlets cannot agree is
established, who broke what others carried, how much of the record is
accusation, what rests on a gazette notice or a judgment — is arithmetic
over the verified findings, and `evidence/patterns.py` does it with no
model. Each pattern carries the numbers it counted and fires only on the
shape it names (a share needs at least three findings). They reach the
writer as `P` entries in the same ledger, labelled as counted, with the
sources of the findings beneath them, and `check_sentence` holds a sentence
citing one to EVERY number it states — not only the large ones `quality`
counts as figures, because "7 of the 3 findings" is a small number and a
false one.

**A confidence the writer gives itself is not evidence.** "Beneath the
surface" was the digest analyst's: asked for contradictions, coordinated
amplification and "the detail that reframes everything", and left to rate
itself high, medium or low — so a pattern could be imagined and "high"
meant the model was sure, not that the sources agreed.
`from_facts.insights_from_record` writes it from the findings and patterns
(in parallel with the brief, same ceiling): headline and reasoning pass
`check_sentence`, the implication is a labelled assessment held by
`check_assessment`, and the pill is `strength()` — computed from what the
insight cites (any allegation → "Includes allegations"; only patterns →
counted; otherwise the WEAKEST finding decides, because a sentence is only
as strong as its thinnest support). A reasoning or implication that fails
is dropped and the headline kept; nothing surviving, or a thin record,
leaves the analysts' reading in place with the reason in `insights_check`.

**A storyline's record is joined on its items, never on its wording.** The
deep-dive analyst wrote 250-500 words of "how it unfolded" that nothing
checked, and the framework printed it as the evidence under its most
important issue — through `esc()` alone, so the `[n]` markers linkify had
put in it reached the reader as bare brackets (fixed by carrying every
`*_citations` list with its prose into `_evidence_for`; the rule: prose
that travels carries its citations). Now `facts.by_storyline` gives each
storyline the findings at least one of whose sources is an item IN it
(the 8-character ref), so a finding that merely sounds like a storyline is
not evidence its sources said it. `record_for(keep_all=True)` hands the
full list to the join and the pipeline drops it before storing. Every
storyline with a record shows it (`on_record`, `record_summary`, no model);
`from_facts.storylines_from_record` writes a chronology in one call across
all of them, each sentence checked AND allowed to cite only its own
storyline's findings, and replaces the analyst's only where at least two
sentences survive — two accounts of one storyline, one checked and one not,
would read as corroboration.

**A stored report is not a fallback.** The engine kept a July report for
"Mbadi" and one for "Edwin Sifuna", and when a live run FAILED for any name
containing either word it served the stored one as `ok: True` with a small
"stale fallback" pill. A reader who asked for this month got another
period's report as the answer — and it pre-empted `_partial_payload`, which
hands over what the failed run did produce under a banner. Removed with the
`SERVE_PRECACHE_FIRST` switch. A failed run is a failure or an honest partial.

**A sample may not make claims about anyone real.** When the engine cannot
be reached the page shows a sample report. It was about William Ruto —
invented findings, invented quotes attributed to Citizen TV and the Daily
Nation, links to made-up articles on their real domains — and the note
explaining it was appended and then wiped, because the renderer clears its
container first; a reader searching someone else saw it with only a
"Cached / demo" pill. It is now a fictional governor of a fictional county,
every link is on an RFC 2606 `.example` domain, the note is added after
rendering, and `test_the_demo_names_no_one_real.py` checks the sample
against the same outlet lists `reports/outlets.py` classifies the live
corpus with.

**Who is being researched is itself confidential.** `/api/baraza` served,
without a key, a weekly leaderboard of every subject in the database with
counts and sentiment. For this product the list of names is the client's
secret. Removed with the Network tab, and
`test_nothing_names_a_subject_without_the_key.py` now requires the key on
every route but the page and `/api/health` — a rule over all routes, because
the next public endpoint will have another name.

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

**The date of publication decides, and nothing may move it.** An investor
found a one-month report "adding mentions going back to the last
election". Collectors had been CLAMPING: an item older than the window was
given the window's first day, so it passed every period filter downstream
as though it were this month's. Google News answers a keyword search with
its best matches of all time unless told `when:Nd`; YouTube's plain search
is relevance-ranked across years; a comment was dated by its video. Now
`ingestion/base.period_date` drops what predates the period, keeps a real
date exactly, and stamps an undated item `date_estimated` rather than
inventing a day; Google News asks `when:`, YouTube asks for this month's
uploads, and a reaction is dated by itself. Every period read goes through
`db/period.published_between`, which excludes estimated dates — a report
about the period reads what was provably published in it. Migration
`b4c5d6e7f8a9` marks the rows already moved: a `posted_at` exactly equal to
its run's window edge is the clamp's fingerprint.

**Local means Kenyan, wherever it publishes.** The segmentation counted the
Daily Nation as international press: Google News rows carried the outlet's
NAME and the classifier looked for `.ke` DOMAINS, and half of Kenya's
newsrooms are on nation.africa, citizen.digital, capitalfm.africa. A Kenyan
broadcaster's YouTube channel read as a social post; Google News's
"Facebook" outlet read as foreign press. `reports/outlets.py` is the one
classifier — built from what the sources actually returned
(`scripts/data_probe.py`), ordered by evidence, and every call returns the
basis that decided it, a default saying it is one. `publisher()` names one
newsroom once, so GDELT's "nation.africa" and Google News's "Daily Nation"
are one source.

**A redraw must hold still.** Each section that lands redraws the report,
and the redraw started at the Overview tab with everything folded: a reader
on the Sentiment Framework was thrown out of it with every section and once
more at the end, when the final render also dropped the scroll position.
The investor's word for it was "a loop". `readerState()` /
`restoreReaderState()` keep the tab, the open sections and the scroll for
the same subject; a tab the reader CHOSE survives, a default may move.
`test_the_framework_stays_where_the_reader_left_it.py` drives it in a real
browser, because none of it is visible to a string match.

**A storyline handed over without its mentions can only be described by
its size.** `narrative_breakdown` carried a label, a description and a
count — not the mention ids, and no tone. So the framework's 4.0 skipped
every storyline on every live report (it had no way to tell a lever from a
barrier) and printed the analysts' generic risks instead; 5.0's "exact"
grouping by storyline never matched; and 6.0 chose the most important issue
by size, which on a live report was "Playful reactions thread" — emoji
under a video — with the report's people, accounts and dates printed under
it as the issue's. `reports/storylines.py` profiles each storyline from its
own items (tone split, publishers, outlet segments, dated first/peak/last,
representative items, people named in it) once, and 3.0, 4.0 and 6.0 read
the same profile. A storyline is set aside as chatter only when its own
label says so AND it is mostly reactions, and every exclusion is declared.
The importance weight counts a reaction as a quarter of a published item.
Names are merged ("Ruto" into "William Ruto" only when exactly one longer
name contains it), the subject is never a person in his own story, and a
role of "null" is no role.

**A median split measures nothing.** Storyline growth compared the items
before and after the storyline's own MEDIAN timestamp — half on each side
by definition — so every rate was about zero and moved only when items
shared a timestamp, which undated items stamped on the window's last day
did. `narratives._growth` splits the REPORT window by time, counts dated
items only, and carries the two halves so the page can say "12 in the
second half against 5 in the first" rather than a bare "escalating".

**A timeline must read without a cursor.** The key-dates strip was dots
whose dates and events lived in a hover — nothing on a phone or on paper —
and dates written out ("15 October 2026") could not be parsed, so the strip
fell back to even spacing under a timeline whose dates were all there.
Moments are now numbered in date order on the strip and in the list beside
it, `whenOf` reads written-out dates, and an analyst entry dated before
the period ("since he was elected in 2022") is marked `before_period` and
listed as context rather than drawn as an event of the month.

**A Google News link is Google's page, not the article.** The connector
stores `news.google.com/rss/articles/<id>`; fetched as it is, it lands on
Google and yields no body — 0 of 15 measured (`scripts/article_probe.py`),
so the largest news source contributed headlines only. `ingestion/
google_news_links.py` resolves it to the publisher (the page's
`data-n-a-sg`/`data-n-a-ts` attributes posted to `batchexecute`: 15 of 15
resolved, 11 of 15 then gave the article); the `googlenewsdecoder` library
resolved 0 of 15, which is why it is done here. The publisher URL replaces
the stored link, so "open the original" goes to the article. An article is
identified by outlet + headline (`cleaning.identity_text`), not by its whole
text, so the headline copy one run stored is upgraded in place to the full
article a later run fetched (`_store_mentions`, ON CONFLICT DO UPDATE when
the new text is longer; `result_count` still counts only new rows, via
`xmax = 0`), and `pipeline._one_row_per_article` folds pairs stored before
that.

**One year means one year.** A `when:365d` Google News query returns its
hundred most relevant results and relevance is recency: 97 of 100 came from
the last five months (`scripts/period_probe.py`). Periods over 45 days are
also asked month by month with explicit `after:`/`before:` dates
(`orchestrator.period_slices`), which return only that month's items. GDELT
reaches back about three months whatever it is asked, and a long run says
so in `source_reach`. The Wayback Machine is not asked for a Search report
— its captures carry no publication date, so none can belong to a period —
and is kept for the Issue Map; Reddit is not planned without app
credentials. Both are recorded in `sources_not_asked`.

**Never state a cause you have not checked.** This has cost an evening three
times: an operator sent to a Railway "Networking" menu that cannot exist for
a crashed service; a nightly backup diagnosed as holding the wrong hostname
when the real cause was a TLS refusal; and the grounding banner advising
`LOW_MEMORY=false` to an operator who had already set it, because the page
guessed the common cause rather than being told the real one. Where a check
degrades, the code that knows WHY must say so — `grounding.check` returns
`method_reason`, and `/api/health` reports `analysis` so a deployment's real
capability can be confirmed without generating a report.

**The narrowest pool was on the busiest stage.** Sentiment scoring walks
every unscored mention in the window, 25 to a batch, and ran them through
FOUR workers — fewer than the digest's six, on the stage with the most
items. A 1,500-mention backlog is sixty batches: fifteen sequential waves
against a 150s deadline, so it could not finish, the remainder stayed
unscored, unscored mentions carry into the next run, and the backlog grew
on every report while the stage fell further behind itself. The analyst
fan-out ran fifteen sections through eight workers — two waves, the second
of which is wall-clock for nothing. Now 12 / 16 / 14 (scoring, fan-out,
collectors). Every one of these threads is blocked on an HTTP response, not
allocating: the correction `low_memory` already received, applied to the
pools that were still narrow. Width is LATENCY; spend is bounded by
`LLM_MAX_CONCURRENCY`, which is process-wide and enforced in
`llm.call_json`, and a test asserts it still clamps the widened pools. The
per-host gap in `ingestion/http.py` is unaffected, so a polite source stays
polite however wide the pool.

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

**A gate is not a ceiling.** `out_of_time()` stops a stage being ENTERED
and does nothing once inside, so a stage entered at minute 29 of a
30-minute ceiling ran for another twenty — a live run sat at 12/15 sections
for fifty minutes with the last three stages (entity/event resolution, the
sentiment framework, claim verification) each gated on entry and then
unbounded. They now run under `_bounded`, the same runner the analyst
fan-out uses, handed `_time_left()` — what is LEFT of the run's ceiling,
not a fresh budget each, because three stages with the full ceiling is one
unbounded run wearing three hats. Floored at 30s, because handing a stage
zero seconds is a different way of skipping it and the ledger would blame a
timeout for a decision the clock made. Wrapping a block in a closure has
one real hazard — anything assigned inside and read after becomes local,
and the NameError lands long after the tests for that stage pass — so a
test walks the AST for exactly that.

**Order is a fix that speed cannot be.** Six deployments tried to get the
Sentiment Framework onto the page by shortening the queue in front of it —
deadlines on the analysts, ceilings on the tail, wider pools — and a live
report on Rigathi Gachagua still showed "Overview" and "Weekly Dashboard"
and no framework tab, while the run went on behind them. Each fix was right
and none helped, because the framework was still LAST: after the analyst
fan-out, after entity resolution and the knowledge graph, gated on whatever
time those left. It needs none of them — it is arithmetic over the scored
corpus, the storylines and the emergent items, all of which exist before the
first analyst is asked anything. `_build_framework` now runs before the
fan-out, publishes, and runs again after it with what the analysts wrote; it
is never gated on the clock, because a stage that costs milliseconds and IS
the deliverable is the last thing a deadline should take away. When
something the client asked for is late, ask what it actually depends on
before asking how to make the things ahead of it faster.

**Deliver first, enrich after.** Entity and event resolution, credibility,
the knowledge graph, temporal signals, the claim audit and the investigator
serve the PLATFORM — they are how the corpus compounds and how the next run
knows what to ask — and none is a section a client reads first. They held
"Still building this report" open for as long as they took.
`pipeline.run_back_office` runs them after the report is saved and the job
marked done (`run_analysis(..., defer_back_office=list)`), each stage on a
session of its own (an abandoned worker must not hold a session someone else
is using), against `BACK_OFFICE_DEADLINE_SECONDS`, and folds the results into
the stored report. Because a reader can start another report while the last
one's back office runs, `engine/runctx.py` binds each thread to its own run's
ledger and health tracker and `carry()` takes that into pool workers —
otherwise one report's failures land on another's page. `_upsert_entity`
inserts inside a SAVEPOINT for the same reason: the back office is now a
concurrent writer of the same keys.

**Collection had no ceiling, and a budget applied per call is not a budget
per run.** Collection was the longest phase of every run and nothing bounded
it: GDELT is paced at 5s and learns to go slower, and `article_text_max_fetch`
— documented as "max URLs enriched per run" — was applied once per QUERY, so
thirty-two GDELT and Google News tasks could attempt thirty-two times the
documented downloads at twenty seconds each. Now `INGESTION_DEADLINE_SECONDS`
stops it: unstarted tasks become `skipped_deadline` (never an empty result),
in-flight ones stop at their next page or download, and the budget is
claimed per run. Every phase is also timed — `StageLedger.lap()` —
and the operator view shows "Where the time went", because a run that takes
too long has to say WHERE or every fix is a guess.

**A server refused is not a question unanswered.** Kenya Law refused every
search from Railway ("403 Forbidden") and Reddit refuses every keyless
request from a cloud address ("403 Blocked"). The first diagnosis written
here blamed Kenya Law's refusal on the python-requests fingerprint; the probe
below then showed the identical request getting a 200 from GitHub's
datacenter network — the block is on Railway's address. Recorded because it
is this file's own rule, broken by its author: never state a cause you have
not checked. Reddit refuses GitHub's network too, even on oauth.reddit.com
without a token, so app credentials are the only route. Each run asked a
dozen times, printed every URL under "Failed" — and the learning loop read
each refusal as a query that had been asked and found nothing, so two runs
behind a block would retire every one of those queries. Now
`http.remember_refusal` keeps a host's verdict on this SERVER for a few
hours and nothing asks again meanwhile (`fetch_backend` included); Kenya Law
goes through the tiered fetcher (a second attempt with Chrome's TLS
fingerprint, not a known fix); Reddit signs in app-only when
`REDDIT_CLIENT_ID`/`SECRET` exist;
and `learning.recall` skips a task that finished empty WITH a recorded
reason. The development sandbox cannot reach either host, so
`.github/workflows/source-probe.yml` asks from GitHub's datacenter network on
every ingestion change and annotates the run — read it before guessing why a
source is down.

**Two readers, one page.** The person who RUNS the engine needs to know which
source refused it, which stage failed, which name was not found. The person
who READS the report — a CEO, a client — needs none of it, and a page that
shows them "Failed: kenyalaw, reddit — HTTPError 403" under a complete report
reads as a broken product. Diagnostics carry `.op-only` and appear only when
"Show operator diagnostics" is on in Settings; downloaded PDFs never include
them. This is not hiding failure: a section with nothing in it is removed
from the reader's view rather than shown empty, so a failed section can never
pass for a quiet one — it is simply not there, and the operator view says
why. Where the reader must know (the model did not answer at all; the run
died partway), they get one plain sentence instead of the stack trace.

**A guard that reports and stops is half a guard.** The grounding banner told
the operator "do not send this to a client until it is clean" and left the
cleaning to them — in a product whose reader never opens a diagnostics
panel. `grounding.redact` now removes each sentence naming someone who
appears in none of the sources, and declares the removal with its sentence;
only ACCUSABLE names (multi-word, capitalised, a distinctive part absent
from every source) are acted on. The live trigger was "sio utumwa'": a
Swahili slogan with its closing quote mark caught in the token, flagged as
an invented person. Quote marks are now stripped before matching, and a
phrase with no capital letter is not a name, whatever an English entity
model labels it.

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
| `INGESTION_DEADLINE_SECONDS` | Ceiling on collection. Default 240. Unstarted queries are recorded as not asked; the corpus compounds, so the next run finds what this one left. |
| `BACK_OFFICE_DEADLINE_SECONDS` | Ceiling on the work that runs after delivery (resolution, knowledge graph, claim audit). Default 900. Nobody waits on it. |

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
`NEWSAPI_KEY`, `SEARXNG_URL`, `X_USERNAME`/`X_EMAIL`/`X_PASSWORD`,
`REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` (a free "script" app at
reddit.com/prefs/apps — Reddit refuses keyless requests from cloud servers,
so without these it contributes nothing in production).

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
- **Is this change better?** → `/api/admin/quality?name=…` lists recent
  reports' measured quality, newest first, with the commit that wrote each.
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
