"""Five crashes a live run reported, once the ledger could name them.

Before the stage ledger these were invisible: each one emptied a section and
the page showed the absence, not the cause. With the failures named, they read
as an ordinary bug list — which is what they always were.
"""

import pytest

from engine import llm, stages
from engine.db.models import NarrativeMetric
from engine.reports import analysts
from engine.reports.sentiment_framework import _as_text_list


@pytest.fixture(autouse=True)
def _fresh():
    stages.reset()
    yield
    stages.reset()


# --- 1. a column that never existed -----------------------------------------

def test_narrative_metrics_are_ordered_by_a_column_that_exists():
    """AttributeError: type object 'NarrativeMetric' has no attribute
    'computed_at' — it never had one."""
    assert not hasattr(NarrativeMetric, "computed_at")
    assert hasattr(NarrativeMetric, "window_end")

    import inspect

    from engine.agents import temporal

    source = inspect.getsource(temporal.take_snapshot)
    assert "NarrativeMetric.computed_at" not in source
    assert "NarrativeMetric.window_end.desc()" in source


# --- 2. a reply of the wrong shape took the whole storyline ------------------

def test_a_deep_dive_that_comes_back_as_a_string_is_skipped_not_fatal(monkeypatch):
    """TypeError: 'str' object does not support item assignment. Assigning into
    the reply raised, and the exception took the section with it."""
    monkeypatch.setattr(analysts.llm, "call_json_untrusted",
                        lambda *a, **k: {"deep_dive": "just a sentence"})
    dives = analysts.analyze_narrative_deep_dives(
        "X", [{"label": "A storyline", "description": "d", "mention_ids": ["m1"]}],
        {"m1": {"id": "m1", "text": "t", "platform": "x", "source_type": "post",
                "author_handle": "a", "engagement": {}}})
    assert dives == []
    failed = [r.name for r in stages.current().failures]
    assert any("narrative_deep_dive" in name for name in failed)
    assert "not an object" in stages.current().records[failed[0]].error


def test_a_well_formed_deep_dive_still_works(monkeypatch):
    monkeypatch.setattr(analysts.llm, "call_json_untrusted",
                        lambda *a, **k: {"deep_dive": {"summary": "s", "quotes": []}})
    dives = analysts.analyze_narrative_deep_dives(
        "X", [{"label": "A storyline", "description": "d", "mention_ids": ["m1"]}],
        {"m1": {"id": "m1", "text": "t", "platform": "x", "source_type": "post",
                "author_handle": "a", "engagement": {}}})
    assert len(dives) == 1 and dives[0]["label"] == "A storyline"


# --- 3. slicing a dict took the client deliverable off the page -------------

@pytest.mark.parametrize("value,expected", [
    (None, []),
    ("a single string", ["a single string"]),
    (["a", "b"], ["a", "b"]),
    ({"risks": ["x", "y"]}, ["x", "y"]),           # nested one level too deep
    ({"theme": "z"}, ["z"]),                        # keyed by theme
    ([{"text": "t"}, {"issue": "i"}], ["t", "i"]),  # objects instead of strings
    ([1, 2, 3], []),                                # nothing usable
])
def test_risks_and_opportunities_survive_any_shape(value, expected):
    """TypeError: unhashable type: 'slice' — the model answered with an object
    where the contract says list, `value[:3]` raised, and the whole Sentiment
    Framework tab disappeared."""
    assert _as_text_list(value) == expected


def test_the_framework_still_builds_when_risks_are_an_object():
    from engine.reports import sentiment_framework as fw

    payload = {"opportunities": {"a": "an opening"}, "risks": {"b": "a danger"},
               "narratives": [], "sentiment_breakdown": {}, "volume_trends": {}}
    issues = fw.build_current_issues(fw.normalise_payload(payload))
    assert issues["potential_levers"] or issues["potential_barriers"]


# --- 4. "failed after 0 attempts" ------------------------------------------

def test_giving_up_reports_what_actually_happened(monkeypatch):
    """A 429 retry does not increment `attempt`, so the give-up message read
    "after 0 attempts" — which sounds like a bug in the caller rather than a
    provider that would not serve us."""
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://provider.test/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "k")
    monkeypatch.setattr(llm, "_throttle", lambda: None)
    monkeypatch.setattr(llm, "OPENAI_COMPATIBLE_TOTAL_BUDGET", -1)
    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")

    with pytest.raises(RuntimeError) as caught:
        llm._openai_compatible_json("prompt", 1000, "some/model")
    message = str(caught.value)
    assert "after 0 attempts" not in message, "this reads as a bug in the caller"
    assert "request(s) sent" in message
    assert "free tier" in message, "name the usual cause"


# --- 5. a paid endpoint refusing every request ------------------------------

def test_the_transcript_call_sends_one_id_named_for_the_endpoint(monkeypatch):
    """id + video_id + post_id together was a guess at the parameter name, and
    the API answered 400 to every transcript request in the run."""
    from engine.ingestion import socialcrawl_connector as sc

    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"data": {"transcript": "spoken words"}}

    def capture(url, params=None, headers=None, timeout=None):
        seen["params"] = params
        return Resp()

    monkeypatch.setattr(sc.http, "get", capture)
    connector = sc.SocialCrawlConnector.__new__(sc.SocialCrawlConnector)
    connector.api_key = "k"
    assert connector.fetch_transcript("youtube", "abc123") == "spoken words"
    assert seen["params"] == {"video_id": "abc123"}


def test_a_rejected_transcript_returns_none_and_records_why(monkeypatch):
    from engine.ingestion import socialcrawl_connector as sc

    class Resp:
        status_code = 400
        text = "Bad Request"

    monkeypatch.setattr(sc.http, "get", lambda *a, **k: Resp())
    connector = sc.SocialCrawlConnector.__new__(sc.SocialCrawlConnector)
    connector.api_key = "k"
    assert connector.fetch_transcript("youtube", "abc") is None
    assert "400" in connector.last_error, "a systematic 400 must not look like a video with no transcript"


def test_the_run_stops_asking_after_repeated_transcript_refusals():
    """A wrong parameter or an exhausted plan fails identically for every
    video; paying that latency once per video buys nothing."""
    import inspect

    from engine import pipeline

    source = inspect.getsource(pipeline._fetch_transcripts_for_top_videos)
    assert "consecutive_failures" in source
    assert "consecutive_failures >= 3" in source
