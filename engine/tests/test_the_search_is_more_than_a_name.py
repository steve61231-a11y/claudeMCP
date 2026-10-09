"""Every free source was being asked one question: the subject's name.

`queries.py` holds fifty discovery probes — business, legal, adverse,
statements, affiliations — seven timespan probes written specifically to
defeat recency ranking, plus aliases, titles, honorifics and Swahili terms.
Its own comment explains why:

    "A single name query returns whatever is trending today — a narrow slice
     of a subject's public footprint."

All of it was wired into `discovery_variants()`, and `discovery_variants()`
is called from exactly one place: the SearXNG discovery task, which is
created only when `enable_discovery and searxng_url`. GDELT, Google News,
Reddit and YouTube each got `query=politician.name` and nothing else.

So without one optional, separately-hosted service, the entire search was a
name typed into four sources. A subject's court record, company
directorships, earlier career and anything not in this week's news were
unreachable by construction — and a report built on that looks exactly like
a report about someone with no such record, which is the one thing this
codebase exists to prevent.

The investigator's follow-up queries had the same single consumer. They were
written to `politician.investigation_leads` after every run and read back
only by the discovery layer, so with SearXNG off the loop that is supposed to
make run two smarter than run one was open at both ends.
"""

import pytest

from engine.ingestion import queries


class _Subject:
    """The shape `queries` reads. Not a DB row — these are pure functions."""

    def __init__(self, name="Musalia Mudavadi", aliases=None, titles=None,
                 leads=None, swahili=None, subject_type="politician"):
        self.name = name
        self.aliases = aliases or []
        self.titles = titles or []
        self.investigation_leads = leads or []
        self.swahili_terms = swahili or []
        self.subject_type = subject_type


def _probes(variants):
    return [v for v in variants if v.startswith('"')]


# --- the defect ---------------------------------------------------------------

@pytest.mark.parametrize("source", ["google_news", "gdelt", "reddit", "youtube"])
def test_a_free_source_asks_more_than_the_name(source):
    out = queries.connector_variants(_Subject(), source)
    assert len(out) > 1, f"{source} is still being handed a single bare-name query"


@pytest.mark.parametrize("source", ["google_news", "gdelt", "reddit", "youtube"])
def test_every_source_reaches_the_record_not_just_the_news(source):
    """At least one probe per source. Without this the expansion is only
    different spellings of the name, which buys nothing — the probes are what
    reach the court record, the directorships and the earlier career."""
    assert _probes(queries.connector_variants(_Subject(), source)), (
        f"{source} asks nothing beyond identity; the record stays invisible")


def test_the_bare_name_is_always_first():
    """This can only add to what a source already returned. If the plain name
    were ever dropped, the expansion would be a regression wearing a fix."""
    for source in queries.SOURCE_VARIANT_BUDGET:
        out = queries.connector_variants(_Subject(), source)
        assert out[0] == "Musalia Mudavadi"


# --- the loop that had no consumer -------------------------------------------

def test_an_investigators_lead_reaches_a_source_that_is_not_searxng():
    """The whole learning loop. Leads were written every run and read only by
    the discovery layer, so with SearXNG unconfigured — which it now is —
    nothing a previous run learned influenced the next one."""
    subject = _Subject(leads=["Mudavadi KICC handover France summit"])
    for source in queries.SOURCE_VARIANT_BUDGET:
        out = queries.connector_variants(subject, source)
        assert "Mudavadi KICC handover France summit" in out, (
            f"{source} ignores what the last run learned")


def test_a_lead_outranks_a_generic_probe():
    """A lead is a question this subject actually raised; a probe is what we
    ask about everyone. On a tight budget the specific one has to win."""
    subject = _Subject(leads=["Mudavadi KICC handover France summit"])
    out = queries.connector_variants(subject, "youtube")      # the smallest budget
    assert "Mudavadi KICC handover France summit" in out


# --- rationing ---------------------------------------------------------------

def test_identities_cannot_crowd_out_the_probes():
    """Concatenate-then-truncate looks right and is not: a subject with
    several aliases and titles fills the budget with spellings of their own
    name before a single probe is reached, and the source then asks nothing
    about the record. Every category needs guaranteed room."""
    crowded = _Subject(
        aliases=["Mudavadi", "Musalia", "MM", "Mudavadi Musalia"],
        titles=["Prime Cabinet Secretary", "Cabinet Secretary for Foreign Affairs",
                "Deputy Party Leader"],
        swahili=["Waziri Mkuu wa Baraza la Mawaziri"])
    for source in queries.SOURCE_VARIANT_BUDGET:
        out = queries.connector_variants(crowded, source)
        assert _probes(out), f"{source}: aliases and titles took the whole budget"


def test_a_budget_of_one_is_the_old_behaviour():
    """An explicit way back to a single query per source, for a host that
    cannot take the traffic."""
    assert queries.connector_variants(_Subject(), "gdelt", budget=1) == ["Musalia Mudavadi"]
    assert queries.connector_variants(_Subject(), "gdelt", budget=0) == ["Musalia Mudavadi"]


def test_a_source_nobody_budgeted_for_is_not_expanded():
    """Unknown sources default to the old single query rather than inheriting
    somebody else's budget and quietly hammering a host that was never
    measured."""
    assert queries.connector_variants(_Subject(), "some_new_source") == ["Musalia Mudavadi"]


def test_the_budgets_respect_the_rate_limits():
    """These numbers come from ingestion/http.py, not from preference. GDELT
    is paced at one request per five seconds, so its budget has to stay below
    the sources that are not paced at all."""
    from engine.ingestion import http

    budgets = queries.SOURCE_VARIANT_BUDGET
    assert budgets["gdelt"] < budgets["google_news"], (
        "GDELT is paced at ~1 request/5s and Google News RSS is not paced at "
        "all; budgeting them alike spends the run's time in the wrong place")
    assert http.HOST_MIN_INTERVAL["api.gdeltproject.org"] >= 5.0


# --- successive runs ----------------------------------------------------------

def test_two_consecutive_runs_share_no_probe():
    """Otherwise every run asks the same six questions forever and a corpus
    that is designed to compound has nothing new to compound with."""
    first = set(_probes(queries.connector_variants(_Subject(), "google_news", rotation=0)))
    second = set(_probes(queries.connector_variants(_Subject(), "google_news", rotation=1)))
    assert first and second
    assert not (first & second), f"runs 1 and 2 repeat: {first & second}"


def test_the_whole_probe_list_is_swept_in_a_bounded_number_of_runs():
    """A rotation that never completes a lap leaves whole categories — the
    legal probes, say — permanently unasked."""
    asked = set()
    for run in range(30):
        asked.update(_probes(queries.connector_variants(_Subject(), "google_news", rotation=run)))
    total = len(queries.DISCOVERY_PROBES) + len(queries.DISCOVERY_TIMESPAN_PROBES)
    assert len(asked) == total, f"only {len(asked)} of {total} probes are ever asked"
