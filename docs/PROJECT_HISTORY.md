# Why this codebase is shaped the way it is

Decisions and the failures that caused them. Code says what it does; this says
why, which is the part that is lost when a session ends and the next person
"tidies up" something load-bearing.

Read alongside the test docstrings — most open by describing the live failure
they were written for.

---

## The founding rule

**A section that failed must never look like a section with nothing to say.**

A report where the model was down and a report about a genuinely quiet subject
produce the same empty page. Every honesty mechanism here exists because that
ambiguity was actively misleading a reader who was going to make decisions
from it:

- `engine/stages.py` — a ledger of what ran, what failed, what was empty, and
  the real exception. Rendered on the page, not swallowed.
- `engine/health.py` — how many model calls succeeded. A run where 139 of 140
  calls failed is not an analysis and says so in those words.
- The "counted, not read" banner — sections built by counting platforms and
  dates rather than by a model reading anything are labelled. Tone and stance
  there are explicitly **not** established.
- `report_grade()` — anything not served by Anthropic is stamped test-grade,
  because the anti-hallucination work is tuned against that model and this
  product is meant to be sold.

## Analysis decisions

**Evidence-first, never from memory.** `GROUNDING_RULES` in
`reports/analysts.py` forbids biographical or status claims from model
knowledge. Everything must trace to a corpus item, tagged `[ref=…]`. This is
why citations exist at all, and why they leak into prose — free-text fields
have no `quotes` array to carry them.

**Triangulation over repetition.** `reports/triangulation.py` counts distinct
*outlets*, not distinct mentions. One story syndicated thirty times is one
source. Verdicts and timeline events carry a corroboration badge, so a reader
can see what rests on a single report.

**House style.** The Economist's: declarative, no hedging, no first person.
`reports/style_check.py` flags violations rather than rewriting them —
consistent with never silently altering model output.

**Floors, always labelled.** When a model cannot produce a section, a
deterministic fallback fills it (`issue_floor.py`, `report_floor.py`,
lexicon sentiment). Always marked `derived`, never presented as analysis.

**Breadth over depth in the analyst window.** Full-article enrichment turned a
60k window into nine articles out of 661 mentions — faithful analysis of 1.4%
of the corpus. Per-item budgets are capped so many voices get in; the whole
body still reaches the digest.

## Infrastructure decisions

**One HTML file, no build step.** `web/pulse_app.html` is the entire frontend,
served by the backend. No npm, no bundler, nothing to break between "works
locally" and "works deployed". `window.ZENITH` exports the render functions so
the PDF exporter reuses the *same* code the browser runs — a PDF cannot show
something the live page would not.

**Chromium via `--print-to-pdf`, not Playwright.** Playwright is not in the
deployed requirements and pulls its own browser download. The system package is
one apt line and about a tenth the size, which is what makes shipping this to a
2GB instance reasonable.

**Everything the run writes is purgeable.** `engine/admin/purge.py`, with
rails: an explicit confirmation phrase, a dry run, `llm_usage` preserved
(it is the record of what was spent), `alembic_version` never touched.

## Bugs that changed the architecture

**The strangled run.** Reasoning models spend their token allowance thinking
before answering, so budgets sized for the JSON returned empty. Those empties
counted toward the circuit breaker, which shrank the time budget, which made
the next call fail, which opened the breaker — and every remaining section was
refused without being sent. One under-budgeted call took down a whole run and
the report blamed the provider. Fixed by separating "the provider would not
serve us" from "we asked for the wrong thing", and by adding thinking headroom
in one place (`llm.budget_for`) rather than at 20 call sites.

**Credit as a resource, not just a price.** Providers reserve `max_tokens`
worth of credit for the whole time a request is in flight. Raising the ceiling
for reasoning models multiplied what a parallel fan-out held, and a working
account started returning HTTP 402. `max_tokens` is now understood as money.

**The report that never finished.** The analyst fan-out had a deadline; the two
stages after it did not, and neither did resolution, the knowledge graph, the
sentiment framework or verification. A run reached 12/15 sections and stopped —
not failing, just never coming back. `try/except` does not bound a call that
never returns. Everything now runs under a ceiling.

**The subject held hostage.** A job whose thread died stayed `"running"`
forever, and the dedupe that stops two pipelines racing handed every new
request back to the corpse. Age was the wrong test; silence is the right one.

**"article" is not a table.** GDELT stores news as `RawMention` rows whose
`source_type` is `"article"`, exactly like real `Document` rows. Inferring the
table from that label wrote a mention's id into `event_evidence.document_id`,
Postgres refused the flush, and — because the guard caught the exception
without rolling back — the poisoned session killed the whole report rather than
one section. Corpus builders now carry an explicit `kind`.

**Sources refusing us.** GDELT meters ~1 request / 5s per IP and the whole
service is one IP. Nothing enforced a gap, so the free news backbone returned
nothing and reports fell back to YouTube video titles. `ingestion/http.py` now
paces per host and widens the gap when refused.

**The PDF of source code.** The exporter spliced its boot script at the *first*
`</body>` — which was inside a JavaScript string, in the client-side download
helper. Its `</script>` closed the page's script early and 187,000 characters
of JavaScript rendered as body text: an 18-page PDF of this app's own source.
Two independently reasonable changes colliding.

## The recurring mistake

Three times in one working session, a fix was applied to the instance rather
than the rule:

1. The citation pattern learned `[ref=x]`, then `(ref=x)`, then `(ref=x, y)` —
   three rounds, each teaching it one more spelling.
2. Prose fields were linkified on the issue map and not the report, because the
   leak had only been *seen* on a map — though both run the same analysts under
   the same grounding rules.
3. A deadline was added to the analyst fan-out while the stages immediately
   after it stayed unbounded.

The tests now enforce rules rather than instances: one seeds a citation into
every prose field of both report types, another greps the whole codebase for
model calls made without a reasoning-aware budget. That is the habit worth
keeping — it is the difference between fixing something once and fixing it
four times.
