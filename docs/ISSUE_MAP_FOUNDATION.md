# The Issue Map — what is wrong with it, and the rebuild

Written 2 November 2026, the same exercise `docs/FOUNDATION.md` was for the
Search report: measure what is actually wrong before adding anything, kill
what is dead, find the sources no one else reads, and rebuild in phases that
can each be checked. The founder's brief: *"We can't offer the same thing
that Gemini and other AI models can just offer in one search. Ours need to be
in-depth … reaching sources that the normal LLMs can't reach."* And: do not
touch the Search report while doing it.

**What an Issue Map is for.** One question: *how is this person connected to
this issue?* — `Kenya Government × the Safaricom share sale`, `Okiya Omtatah
× IMF`. Any chatbot can write a paragraph that sounds like an answer. The
answer a client pays for is a **chain of evidence**: who did what, on which
record, on which date, and who stands where — with the next decision point
dated. That is the product this plan builds towards.

---

## 1. What was measured wrong

The founder's live map, *Kenya Government × "Kenyan Court stopping the Kenyan
government's sale of its Safaricom shares to Vodacom"*, 90 days: **16
documents examined, 6 on topic, every one of them YouTube** — on one of the
year's largest business stories, which every Kenyan newsroom covered.

### A. Collection asked questions no source can answer  *(the root cause)*

1. **The issue is searched as one quoted sentence.** `queries.issue_variants`
   returns the issue as typed, and every free source — GDELT, Google News,
   Reddit, YouTube — is asked `"Kenya Government" "Kenyan Court stopping the
   Kenyan government's sale of its Safaricom shares to Vodacom"`. No article
   contains that sentence. GDELT and Google News return nothing, and the
   corpus falls back to whatever YouTube matched loosely.
2. **Decomposition picks the wrong anchor.** `decompose` takes the first
   capitalised span, "Kenyan Court", as the issue's name, so every research
   dimension asks about "Kenyan Court" (`"Kenyan Court" explained`) instead of
   the distinctive names, Safaricom and Vodacom. It also keeps the bare
   demonym **"Kenyan" as an identity**, so any Kenyan document can count as
   "about the issue".
3. **"History" is three fixed years.** The history dimension asks for 2019,
   2020 and 2021 whatever the issue and whatever the window.
4. **SocialCrawl on the map has no ceiling** (`credit_budget=0.0`, meaning
   unlimited): four pairs × the 50-credit composite plus a comment call per
   post. The Search report is capped at 300.

### B. The map reads coverage, never the records where connections are made

A connection between a person and an issue is made on paper: a party to a
court case, a gazette notice of an appointment, a speech in Parliament, a
tender award, a company directorship. The map reads news and social posts
*about* those records and never the records themselves. A chatbot can do
that much. Nothing a client cannot get elsewhere is in the corpus.

### C. Most of what the client reads is unchecked

- The **verdict**, **involvement**, **tension** and **sub-issues** are
  written by analysts over a digest, with no sentence checked against the
  record. Only the connection section (built 30 Oct) and the scenarios
  (31 Oct) stand on verified findings.
- **Claim verification never runs on the map**, so the Data overview prints
  "claim verification: not run" on every map a client receives.
- **The map is never quality-measured.** The Search report carries
  `quality` (`reports/quality.py`) on every run; the map carries nothing, so
  no change to it can be shown to be better.

### D. The product shows its seams

- **PDF export fails for clients**: the server has no browser installed
  ("PDF unavailable (HTTP 503) — saved … .html instead"), and installing one
  costs memory on a service that is already crashing for memory.
- **Framework and Intersection detail now say the same things twice**
  (involvement, tension, sub-issues, the graph), in two tabs.
- The **graph's dotted lines are co-occurrence**: two names in one article.
  That is not a connection, and a client reads a line as one.

### E. Dead code — removed in this commit

- `relevance.filter_corpus` and `relevance.score_document`: the map moved to
  pools (`partition_corpus`) and never called them again. Their tests pinned
  real rules (market anchoring, one-half documents) against code that no
  longer ran; they now run against `partition_corpus`.
- `POOL_OFF` and `POOL_MEANING`: never read.
- **Moving those tests onto the live code found a live defect.** The Kenya
  check matched `"ke."` as a *substring* after normalisation, so "ke" inside
  "keeps" or "make" passed a New Jersey senate story as Kenyan background.
  `mentions_any` now matches whole words, and `.co.ke`/`.go.ke` match as
  domain words.

---

## 2. The idea: the connection is a path of evidence

Every other tool answers "how is X connected to Y?" with prose. This one
will answer with a **path**, and every step on it rests on a record:

```
Kenya Government ──(Gazette Notice 1234 of 2026: Treasury notice of disposal)──▶ Safaricom shares
Safaricom shares ──(High Court Petition E567 of 2026: petitioner, respondents)──▶ High Court ruling, 25 Sep
High Court ruling ──(Civil Appeal E890 of 2026: appellant Attorney General)──▶ Court of Appeal, 14 Oct
Court of Appeal ──(Cause list: mention before the Supreme Court, 12 Nov)──▶ NEXT DECISION POINT
```

Each edge is **typed** (party to, appointed by, spoke on, awarded, director
of, relative of) and **sourced** (case number, gazette notice, Hansard date,
tender id). Where no path exists, the map says so and says how far the
record reaches. A dotted "co-mentioned" line is never drawn as a connection.

The sources that make this possible are public and structured, and almost
nobody reads them together. The three in bold are verified to exist; all of
them must be probed from our servers before they are built on:

| Source | What it gives the map | Status |
|---|---|---|
| **Kenya Law — judgments and daily cause lists** (new.kenyalaw.org) | Parties to cases (who sued whom), case numbers, and the **next hearing date** for a case already found | Exists. Railway's address is refused (403); GitHub's is not — see Phase 1 |
| **Kenya Gazette** (via Kenya Law) | Appointments, revocations, disposals, commissions | Already collected for the Search report; same block applies |
| **Hansard via Mzalendo** (info.mzalendo.com JSON API; Odnelazm, open source, parses it) | What the principal and MPs said in Parliament about the issue, verbatim and dated; committee business | API confirmed; terms of use to read |
| **Public procurement (PPIP, tenders.go.ke/ocds)** | Tender awards in OCDS: who bought what from whom, for how much | Bulk JSON/CSV, refreshed monthly |
| ICIJ Offshore Leaks (ODbL / CC BY-SA) | Offshore entities and officers that name a Kenyan principal | Bulk download, ~70 MB; attribution required |
| Wikidata (CC0, SPARQL) | Positions held, party, relatives, boards, as a structured graph | Free |
| World Bank projects and documents API | International financing tied to the issue (programmes, conditions, dates) | Free |
| OpenSanctions (PEPs, sanctions) | Politically exposed persons and their associates | **Licence needed for commercial use** — founder's decision |

This is "outside the box" in the plain sense: a researcher would need a
law clerk, a parliamentary researcher, a procurement analyst and an
investigative journalist to assemble one map this way. The engine can
assemble it in one run because each of those records is machine-readable.

---

## 3. The phases

Each phase ends in a measured result on the **benchmark set** (section 4),
not in a map that looks better by eye. Nothing in the Search report changes.

### Phase 0 — Make it collect  *(small; the root cause)*

- Split an issue into **search terms a source can match**: distinctive names
  (Safaricom, Vodacom), short noun phrases ("Safaricom shares sale",
  "Safaricom stake"), acronyms. Never the sentence; never a demonym alone.
- Fix the decomposition anchor (distinctive names before generic ones), and
  date the history dimension relative to the window.
- **Cap SocialCrawl on the map** at the Search report's 300 credits, with
  the spend recorded.
- **Measure the map**: an `issue_quality` record on every map — documents
  examined and on topic by source, primary-record share, connection
  findings, how much of the page rests on the record and how much does not,
  sentences removed by the checks. Stamped with the commit, listed at
  `/api/admin/quality`.
- *Done when:* the benchmark Safaricom map reads news from at least three
  Kenyan outlets and the corpus is not majority YouTube.

### Phase 1 — Reach the records  *(the sources)*

- **Probe every source in section 2** from GitHub's network
  (`source-probe.yml`) and from Railway, and record which answer where. Never
  build on a source we have not seen answer.
- **A primary-record mirror.** Kenya Law refuses Railway and answers GitHub,
  so the records are collected by a scheduled GitHub workflow into Neon
  tables (`primary_records`: kind, number, date, parties, text, URL) and the
  map reads them from the database. This also fixes the same block for the
  Search report's gazette and judgments, without touching its code.
- Connectors, in order of value: Kenya Law cause lists and judgment parties
  → Hansard (Mzalendo) → PPIP awards → ICIJ → Wikidata → World Bank.
- *Done when:* at least three benchmark maps carry a court record, a Hansard
  entry or a tender that names the principal.

### Phase 2 — The connection path  *(the differentiator)*

- Typed, sourced edges from those records: party-to-case, appointed-by,
  spoke-on, awarded, director-of, relative-of. Each edge carries its record
  id and date. A model may help *read* a record; it never creates an edge
  the record does not state (the fact store's verbatim rule).
- **Shortest evidenced paths** from principal to issue, drawn as a chain
  with the record under every step, and "no evidenced path within N steps"
  said in words when there is none.
- The co-occurrence graph stays as navigation, labelled as co-mention, never
  as connection.
- *Done when:* every edge on a benchmark map opens the record it rests on.

### Phase 3 — Check everything the client reads

- The **verdict, involvement and tension** are written from the record the
  way the Search report's brief is (`from_facts`): every sentence cites a
  finding or a path edge and is checked with no model. The analysts' version
  stays only as a labelled fallback when the record is thin.
- **Evidence status stamped on every framework section**, as on the Search
  report: on the record / partly / counted / the analyst's reading.
- Run claim verification on the map, or remove the chip.
- *Done when:* `issue_quality` shows the share of client-facing sentences on
  the record rising across the benchmark set, and no unchecked prose sits
  above a checked section without a label.

### Phase 4 — The calendar ahead

- For every case found, read its **next hearing** from the cause lists;
  from Hansard and Parliament's order papers, the **next sitting** on the
  issue; from the Gazette, statutory **deadlines**. Each date carries its
  record.
- These become the trigger of the scenario tree (the next decision point)
  and the spine of Sequencing, replacing anything inferred.
- *Done when:* the benchmark court-case maps show a dated next step taken
  from a cause list, not from a news sentence.

### Phase 5 — One deliverable

- **Merge Intersection detail into the Framework**; the detail becomes "The
  working" (method, sources, what was set aside), operator-first.
- **PDF without a server browser:** a print stylesheet and the browser's own
  "Save as PDF", so no Chromium and no memory on Railway.
- **What changed since the last map** on the same pair — new records, moved
  dates, paths that appeared or broke — because the corpus compounds.

---

## 4. How we will know

**Benchmark set**, run before and after each phase, with the numbers
recorded here:

1. Kenya Government × the Safaricom share sale (a court case, an asset sale)
2. Okiya Omtatah × IMF (a petitioner and an international lender)
3. William Ruto × SHA (an executive and a programme)
4. A county governor × a county health levy (a sub-national issue)
5. A company × KRA (business, regulator)

**Per map:** documents examined and on topic by source; Kenyan outlets
read; primary records read and their kinds; connection findings; evidenced
path edges; share of client-facing sentences on the record; sentences
removed; dated next steps and where each came from; credits and tokens
spent; wall-clock time.

---

## 4a. Progress

- **Phase 0 — done (2 Nov 2026).**
  - An issue is searched by its anchors and the phrases round them
    (`decompose.search_terms`): the Safaricom sentence becomes "Safaricom
    shares", "Safaricom sale", "Safaricom", "Vodacom" — never the
    sentence, never "Kenyan", never "Kenyan Court" beside a real name.
    The news sources also ask for the issue alone, for its background.
  - The research dimensions anchor on the distinctive name, and history
    asks for the three years before the window.
  - A new principal is typed from its name, so "Kenya Government" is no
    longer searched as "Hon Government", and it is searched as the press
    prints it ("Kenyan Government", "Government of Kenya").
  - SocialCrawl on the map: page by page, every page's cost counted
    against `ISSUE_MAP_CREDIT_BUDGET` (300) before it is spent; no
    50-credit composite, no comment threads; spend on the operator's card.
  - Every map carries `quality` (`reports/issue_quality.py`): what was read
    by kind and segment, Kenyan outlets, full text, official records,
    reactions, video share, the pools, sentences on the record against
    unchecked, removals, spend, the commit. Listed at `/api/admin/quality`
    beside the reports, labelled `issue_map`.
  - Found on the way, and fixed: the map's GDELT path still clamped dates
    into the window (the Search report's 21 Oct defect); `_as_corpus`
    dropped `date_estimated` and the full-text flag; the identity guard's
    removals were streamed but never stored with the map.
  - *Measured on the benchmark set:* waiting on OpenRouter credits. The
    figures go here when they exist.

- **Phase 1 — the records are read (2 Nov 2026).** What the probes found
  changed the plan, and the change is recorded rather than smoothed over:
  - **No mirror was needed.** The live server measures its own reach
    (`evidence/record_reach.py`, `/api/health` → `record_reach`) and on
    2 Nov reached every record host, Kenya Law's new search API included.
    The refusal that motivated the mirror was the OLD Kenya Law site.
  - **Ruled out by the probe:** Mzalendo (ignores `search` and `ordering`;
    newest sitting July 2025), ICIJ (a JavaScript challenge), PPIP (a
    single-page app with no API we could see). Kenya Law's cause lists and
    document pages sit behind a JavaScript challenge too, so the court
    record is the search card.
  - **Built, each against a contract the probe observed:**
    `ingestion/kenya_law_records.py` (the search API's cards: parties as
    printed, case number, court, date, the source's own snippet);
    `ingestion/parliament_records.py` (the Hansards of the window, read as
    PDFs, each speech the part of the member's turn on the issue under
    the member's name as printed, with the order of business it was made
    under; order papers ahead as dated next steps);
    `ingestion/world_bank_records.py` (projects and documents, for money,
    energy, transport and international issues). `issue_map.acquire_records`
    runs them in parallel; one source down costs only itself, and every
    set-aside is counted with its reason.
  - **Run for real, on six issues of six kinds** (SHA, school fees, the
    housing levy, Mau forest evictions, the IMF loan, the Safaricom share
    sale — `scripts/record_probe.py` in `source-probe.yml`): every issue
    reached Kenya Law and Parliament; 8 Hansards read per issue, 1-8
    speeches on each. The first run's defects were general, and fixed as
    rules, not cases: a speaker label that ran to a colon in the speech;
    whole turns kept for one sentence; page furniture quoted as a member;
    family, estate and private disputes; party initials read as acronyms
    ("JMA v SHA"); World Bank items from 1964.
  - **An issue is its name AND what is said about it**
    (`reports/issue_match.py`). The run showed the map matched an issue
    on its name alone — "emulate Safaricom" under the share sale, the
    Mau Summit ROAD under Mau forest evictions. One rule now decides "about
    the issue" for the pools, the connection and the records, and a
    document naming only the name is set aside under its own reason.
  - *Done when* (from section 3): three benchmark maps carry a court
    record, a Hansard entry or a tender naming the principal. Waiting on a
    live map.

- **Phase 2 — the connection path (2 Nov 2026).** `evidence/paths.py`: typed,
  sourced steps the records state — party to a case (sides from the title,
  one step per case however many rulings), speaker in the House, a lender's
  borrower or agency — with the office standing for a government or a
  ministry said as the basis. Records from earlier maps of the same
  principal join when they name this issue. On the page as "The record that
  ties them", in the graph as teal record lines, and in `issue_quality`.
  Live: Kenya Government × Safaricom share sale — respondent in Petition
  E051 of 2026 (4 rulings); National Treasury × housing levy — respondent
  in Petition E181 of 2023. Found on the way: co-mention lines were drawn
  solid, like stated links; now dotted.
- **Phase 3 begun — every section says what it rests on.**
  `reports/issue_evidence_status.py` grades the verdict, the connection,
  the path and each framework section (on the record / partly / counted /
  the analysts' reading), stamped under each heading.
- **Phase 3 — the answer from the record.** `reports/verdict_from_record.py`
  writes the map's opening answer from verified findings naming both and the
  roles public records state, each sentence checked with no model
  (`from_facts.check`); the analysts' verdict stays beneath it as their
  reading, or leads, labelled, when the record is too thin. Still to do in
  Phase 3: involvement and tension (the stakeholder positions) from the
  record.

## 5. What the founder decides

- **OpenRouter credits** — every model-written part of the map stops
  without them (the 14/14 failed passes on 1 Nov were exactly this).
- **Railway** — resolved 2 Nov: the native GitHub connection deploys
  again, and `deploy.yml` verifies the live commit.
- **OpenSanctions** — the PEP data needs a commercial licence; worth it or
  not.
- **Approval of the phases**, in this order or another.
