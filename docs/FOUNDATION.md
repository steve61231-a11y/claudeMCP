# The foundation — what is wrong, why, and the plan

Written 22 October 2026 after the founder's instruction: *"We cannot allow any
room for hallucination… we need a strong, very strong, concrete foundation…
even if we add more users, even if we add more variables to search for."*

Everything below marked **measured** was checked on that date, with the
command or probe named, so it can be re-checked rather than believed.

---

## In one page

The reports got worse as features were added because every feature added
**another AI writing freely over the same thin pile of headlines**. More
writers meant more places to invent, more repetition, and more things that
could fail and vanish — while the material underneath never got richer.

The fix is not more AI and not more databases. It is to make the **evidence**
the centre of the system:

1. **Read the articles, not the headlines.** Today the largest news source
   gives us headlines only. That is fixable now (measured below).
2. **Extract facts once, check them mechanically, store them.** One shared,
   verified fact store that every AI reads from — that is the "same context".
3. **Write only from the facts, and check every sentence** — its sources, its
   quotes, its numbers — before a client sees it. Anything that cannot be
   backed is removed, and the removal is recorded.
4. **A fixed report.** The same sections, in the same order, every run. A
   section with little evidence says so; nothing silently disappears.
5. **Runs that survive a restart.** Today a deploy kills any report in flight.

---

## Where we are (measured)

| | Finding | How it was measured |
|---|---|---|
| Size | ~29,000 lines of engine Python, a 6,200-line single-file frontend, 161 test files | `wc -l` |
| Writers | A Search report is written by about 14 separate model calls, each reading its own sample of the raw corpus. 33 prompts exist across the engine | `grep _PROMPT`, `reports/sections.py` |
| Second-hand writing | Summary, risks, opportunities and trends are written from a table of counts and labels only — no source text — and asked for 6–12 items each. The executive brief is written from the other writers' output; the deep insight from a summary of summaries | `reports/sections.py` (`_context_blob`), `analysts.py` |
| Checking | Names, quotes and identity terms are checked. **Claims and numbers in prose are not** — the code says so: *"An analyst can write any sentence it likes into the executive brief and nothing mechanical ever looks at it."* | `reports/grounding.py` docstring |
| Google News | **0 of 15** items gave an article body: every link is a `news.google.com` redirect and every fetch landed there. Decoding the link to the publisher (Google's signature + batchexecute step) worked **15 of 15**, and then **11 of 15** gave the full article (median ~2,400 characters). The `googlenewsdecoder` library decoded **0 of 15** | `scripts/article_probe.py`, run 22 Oct from GitHub's network |
| GDELT | **12 of 12** publisher links gave the full article (median ~1,900 characters) — but GDELT rate-limits; a second run got no items | same probe |
| Other sources | Reddit refuses without app credentials; Kenya Law and the Gazette refuse Railway's address (they answer GitHub's); SocialCrawl has no credits; the X scrapers need an account and are off; Wikipedia is off | `source-probe.yml`, `config.py`, CLAUDE.md |
| Model | Every role — analysis and bulk work — runs on DeepSeek V4 Flash through OpenRouter; the engine itself marks this test-grade | `deploy-drift.yml` → `/api/health` |
| Runs | A report runs as a background thread inside the web server (one replica). A deploy or crash kills every run in flight | `api_server.py` (`threading.Thread`), `railway.json` |
| Disappearing | 29 report sections render only when their data exists, and the client view hides any section that comes out empty — so the report's shape changes from run to run | `grep "return null"`, `.sect[data-empty]` rule |
| Dead weight | Seven API endpoints nothing on the site calls (simulate, tipline, compare, alerts, livefeed, graph/connections, network/edge); a Baraza view with no tab; a Neo4j projection that is not provisioned in production; two legacy apps in the repo (`frontend/`, the NestJS `api/`) | `grep` against `web/pulse_app.html`, `intelligence/graph.py` |

So in production a report is built mostly from **headlines, YouTube titles and
comments**, written by a budget model in fourteen independent passes, with no
mechanical check on its claims or numbers. "Plain", "not enough data" and
"sometimes hallucination" are what that design produces — whatever the model.

---

## Your questions, answered

**Do we add more AIs that do different things but have the same context?**
Yes — but fewer writers, not more. Three roles, one shared context:

| Role | Job | Model |
|---|---|---|
| Extractor | Turn each article, post, judgment or notice into facts: who, did what, when, the exact sentence it came from | the cheap model — its output is checked mechanically, so a mistake is caught rather than published |
| Analyst | Write each section from the facts and the computed numbers only, citing fact IDs | the strong model, when you switch — it is a handful of calls, not fourteen |
| Checker | Check every sentence: the cited facts exist, quotes appear verbatim, numbers match what the code computed, names are in the sources | code first; a small fact-checking model (MiniCheck, below) for "is this sentence supported" |

The "same context" is the verified fact store. Today each of fourteen writers
builds its own context from its own sample, which is why sections repeat each
other and occasionally contradict each other.

**Do we separate databases?** No. One Postgres is right at this size, and the
database is not why the reports are weak. What we separate is concerns inside
it — the evidence (items, facts), the reports, and the jobs — and we move runs
onto a job queue kept in that same Postgres, so a restart resumes a run
instead of killing it.

**The cheaper model while testing** is fine, and the new design makes the
switch cheaper: the expensive model is used only for the final writing.

---

## What to cut

Removed from the product, kept in git history. In order of certainty:

1. **Dead code** — the legacy `frontend/` and NestJS `api/` apps, the Baraza
   view, the seven unused endpoints and what only they use, the Neo4j
   projection. Nothing on the site uses them; they only cost attention.
2. **Second-hand writers** — summary, risks, opportunities and trends written
   from counts; the map-reduce digest and the "deep insight" built on it.
   Replaced by analysis over the fact store (phase 3), not removed before.
3. **Sources that cannot work in production** — kept switched off until their
   credentials exist (Reddit app keys, a funded SocialCrawl, an X account),
   instead of failing on every run.

**Your decision:** the Issue Map and Network tabs. My recommendation: keep the
Issue Map and rebuild it on the fact store in phase 3; park Network until the
fact store can say who is connected to whom from evidence rather than
co-mentions.

---

## Open-source projects worth adopting

Researched 22 October 2026; each still to be tried on our own data before it
goes in.

| Project | What for | Note |
|---|---|---|
| [trafilatura](https://github.com/adbar/trafilatura) | Article text extraction | Already in use; the probe shows it works once it gets the real article page |
| Google's own decode step | Google News link → publisher URL | Implemented in the probe; the [`googlenewsdecoder`](https://pypi.org/project/googlenewsdecoder/) library decoded 0 of 15, so we keep our own |
| [MiniCheck](https://github.com/Liyan06/MiniCheck) | Checker: "is this sentence supported by this document?" | EMNLP 2024; small models reported at GPT-4 accuracy for this task. Memory on our instance to be measured |
| [GLiNER](https://github.com/urchade/GLiNER) | Finding people, organisations and positions in text, with labels we choose | Runs on CPU; to be compared against spaCy on Kenyan names |
| [Procrastinate](https://procrastinate.readthedocs.io) | Job queue in Postgres: runs survive restarts, retry, run in a worker | Its maintainers are looking for help — a simpler in-house queue table is the fallback |
| [Langfuse](https://github.com/langfuse/langfuse) | Records every prompt and answer, with cost — so a bad sentence can be traced to the call that wrote it | Self-hostable; a Railway template exists |
| [promptfoo](https://promptfoo.dev/docs/intro/) | Regression tests for model output | Acquired by OpenAI in March 2026; MIT. Our own pytest-based check (phase 0) may be enough |

Primary records — the "I never knew this" material:

| Source | What it holds |
|---|---|
| [PPRA's procurement portal (OCDS)](https://data.open-contracting.org/en/publication/147) | National and county tenders and awards, refreshed daily, JSON; mirrored at [opentender.eu](https://ke.opentender.eu/download) |
| [Mzalendo](https://new.mzalendo.com/data/) | Hansard, bills, motions and votes for the 13th Parliament — what an MP actually said and voted |
| [Laws.Africa content API](https://platform.laws.africa/services/knowledge-bases/) | Legislation in full text |
| [Media Cloud](https://github.com/mediacloud) | Open news archive with an API — its Kenya coverage is unverified |

---

## The plan

Each phase ships only when the quality check (phase 0) shows it is better
than what it replaces. The report keeps working throughout.

**Phase 0 — measure.** A fixed set of test subjects and a quality check run
on every change: share of sentences with a verified source, numbers that do
not match the computed figures, names not in the sources, article-body
coverage, sections complete. Without this, "better" is an opinion.

**Phase 1 — depth of data.** Decode Google News links and read the full
articles. Pace GDELT so it stops being refused. Switch off sources that
cannot work in production. Show, per run, how much of the corpus is full
text versus headline.

**Phase 2 — the fact store.** Extract facts from every item with the exact
source sentence; drop anything whose quote is not in the text; deduplicate
the same story told by several outlets into one event with its sources.

**Phase 3 — write and check from facts.** Every section written from the
fact store and the computed numbers, every sentence checked, unsupported
sentences removed and recorded. The fixed report: same sections every run,
each stating whether its evidence is complete, thin or failed. The
second-hand writers are retired here.

**Phase 4 — durability and visibility.** Runs on a job queue that survives
restarts; every model call traceable.

**Phase 5 — breadth, on the foundation.** Procurement and Hansard; then
companies, CEOs and institutions as subjects through the same pipeline —
the "more variables" you described, added to a base built to take them.

---

## Progress

**22 Oct 2026.** Founder's decisions: keep the Issue Map (rebuild later),
park Network, test subjects Edwin Sifuna and Martha Karua.

- **Phase 0 — done.** Every report carries a measured `quality` block
  (`reports/quality.py`), stamped with the commit that wrote it; operators
  see a Quality check card; `/api/admin/quality` lists reports over time.
- **Phase 1 — started.** Google News links decoded to the publisher and the
  article read (`ingestion/google_news_links.py`); a headline and its
  article are one item, upgraded in place; a long period is collected month
  by month and GDELT's three-month reach is declared, not mistaken for a
  quiet past.
- **Phase 2 — the store exists.** `evidence/facts.py` reads each source once,
  in full, and keeps a fact only if its quote is in the source verbatim;
  invented quotes are counted, not stored. It runs after each report on
  sources not yet read; `/api/admin/facts` shows it. The same story from
  several outlets is one finding with a count of independent ORIGINS — one
  publisher repeating itself, or two printing the same wire copy, is one —
  and takes its weakest member's status.
- **Phase 3 — started.** The report's first section of the Case is now
  "On the record": built from the store with no model writing it. Facts
  are read alongside the analysts (no added wait unless still reading when
  they finish, capped by `FACT_STORE_WAIT_SECONDS`), and each finding shows
  its status ("Alleged — not established" where it is), its independent
  origins and every source's own words.
- **Phase 3 — the brief.** The executive brief is written from that record
  (`reports/from_facts.py`): each sentence names the findings it rests on
  and is checked mechanically — it must cite real findings, draw its
  content words, figures, years and names from them, and attribute
  anything only alleged. Failures are removed and listed for the operator.
  With fewer than three findings, or fewer than two surviving sentences,
  the analysts' brief stands and the reason is recorded.
- **Phase 3 — risks and opportunities.** Written in the same call from the
  same record: each is a checked finding with citations plus an
  assessment labelled as the analyst's judgement, which may reason but may
  not introduce a name, figure or date the findings lack. This retires the
  second-hand writers that produced them from counts. Next: the remaining
  analyst prose (beneath the surface, deep dives) through the ledger.
- **Cuts — done.** Network tab and endpoints, the six endpoints nothing
  called, the public leaderboard (it listed every subject without a key),
  the stored reports served in place of failed runs, the Neo4j projection;
  and, with the founder's approval, the legacy `frontend/` and NestJS
  `api/` apps, `engine/main.py` and its Dockerfile, and the Neo4j modules.
  `docker-compose.yml` now runs the same image as the host. The sample
  report shown when the engine cannot be reached is about a fictional
  person, its links go nowhere real, and it says so above itself.

---

## What only the founder can do

1. **Replace the Railway deploy token** — every deploy since the morning of
   21 Oct has failed: first "Deploys have been paused temporarily", then
   "Invalid RAILWAY_TOKEN" on every push since. Railway → project → Settings → Tokens →
   create a project token; GitHub → Settings → Secrets and variables →
   Actions → `RAILWAY_TOKEN`. Never paste it into a chat.
2. **Decide the Issue Map and Network** (above).
3. **Name the test subjects** — three to five: a politician, a company, a
   CEO, an institution. For one of them, three things you already know that
   a great report should surface. That becomes the "wow" test.
4. Later, not now: free Reddit app credentials; the model budget for the
   final writing step; a Media Cloud account if it covers Kenya.
