"""The dashboard's period-over-period charts must plot real movement.

The client's reference dashboard is built on change between reporting periods:
tone about the subject, which personalities own the conversation, and how the
theme mix shifts. None of that existed.

Worse than absent: the tone chart read payload["timeline"] — the ANALYST's
dated event list, which has no sentiment fields — and plotted zeros on every
report; and the share-of-voice area chart drew
`mentions * (1 + 0.15*sin(period + index))`, a sine wave. It looked like
movement and was invented.
"""

from datetime import datetime, timedelta

from engine.reports.periods import build_period_series

WS = datetime(2026, 2, 1)
WE = datetime(2026, 2, 27)


def _m(i, day, text="Talon speaks on reforms"):
    return {"id": f"m{i}", "text": text, "posted_at": datetime(2026, 2, day),
            "platform": "news", "source_type": "article"}


def test_tone_is_split_by_period_not_averaged():
    mentions = [_m(i, 3) for i in range(4)] + [_m(i + 10, 20) for i in range(4)]
    sentiments = {f"m{i}": {"sentiment": "positive"} for i in range(4)}
    sentiments.update({f"m{i+10}": {"sentiment": "negative"} for i in range(4)})

    series = build_period_series(mentions, sentiments, [], [], WS, WE, buckets=3)

    assert len(series["tone"]) == 3
    assert series["tone"][0]["values"]["positive"] == 4
    assert series["tone"][0]["values"]["negative"] == 0
    assert series["tone"][-1]["values"]["negative"] == 4


def test_an_undated_mention_is_not_assigned_a_period():
    """Guessing a bucket would invent movement that never happened."""
    undated = _m(99, 3)
    undated["posted_at"] = None
    series = build_period_series([undated], {"m99": {"sentiment": "positive"}},
                                 [], [], WS, WE, buckets=3)
    assert sum(p["mentions"] for p in series["tone"]) == 0


def test_personalities_are_counted_by_occurrence_per_period():
    mentions = [
        _m(1, 3, "Talon and Boni Yayi meet"),
        _m(2, 3, "Talon addresses parliament"),
        _m(3, 20, "Boni Yayi returns to the capital"),
    ]
    people = [{"name": "Talon"}, {"name": "Boni Yayi"}]
    series = build_period_series(mentions, {}, [], people, WS, WE, buckets=3)

    first, last = series["personalities"]["periods"][0], series["personalities"]["periods"][-1]
    assert first["values"]["Talon"] == 2
    assert first["values"]["Boni Yayi"] == 1
    assert last["values"]["Boni Yayi"] == 1
    assert last["values"]["Talon"] == 0


def test_theme_bands_keep_the_same_meaning_across_periods():
    """Ranking within each period would make a band change meaning between
    columns, which is worse than leaving a theme out."""
    mentions = [_m(i, 3) for i in range(3)] + [_m(i + 10, 20) for i in range(3)]
    narratives = [
        {"label": "Reforms", "mention_ids": ["m0", "m1", "m2"]},
        {"label": "Opposition", "mention_ids": ["m10", "m11", "m12"]},
    ]
    series = build_period_series(mentions, {}, narratives, [], WS, WE, buckets=3)

    keys = series["themes"]["keys"]
    for period in series["themes"]["periods"]:
        assert list(period["values"]) == keys, "bands are not consistent across periods"
    assert series["themes"]["periods"][0]["values"]["Reforms"] == 3
    assert series["themes"]["periods"][-1]["values"]["Opposition"] == 3


def test_theme_membership_is_exact_not_projected():
    """A narrative already knows which mentions belong to it; nothing is
    modelled, smoothed or inferred."""
    mentions = [_m(1, 3), _m(2, 20)]
    narratives = [{"label": "Reforms", "mention_ids": ["m1"]}]
    series = build_period_series(mentions, {}, narratives, [], WS, WE, buckets=3)
    totals = sum(p["values"]["Reforms"] for p in series["themes"]["periods"])
    assert totals == 1


def test_the_series_needs_no_model_call(monkeypatch):
    from engine import llm

    def boom(*a, **k):
        raise AssertionError("a chart must never cost a model call")

    monkeypatch.setattr(llm, "call_json", boom)
    monkeypatch.setattr(llm, "call_json_untrusted", boom)
    build_period_series([_m(1, 3)], {"m1": {"sentiment": "neutral"}},
                        [{"label": "R", "mention_ids": ["m1"]}], [{"name": "Talon"}],
                        WS, WE)


def test_labels_are_readable_periods():
    series = build_period_series([], {}, [], [], WS, WE, buckets=3)
    assert len(series["labels"]) == 3
    assert "Feb" in series["labels"][0]


def test_the_dashboard_no_longer_fabricates_movement():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text(encoding="utf-8")
    dashboard = html[html.index("function renderWeeklyDashboard"):]
    dashboard = dashboard[: dashboard.index("function searchView")] if "function searchView" in dashboard else dashboard
    assert "Math.sin" not in dashboard, "a chart is still drawing synthetic movement"
    assert "ps.tone" in html and "ps.themes" in html and "ps.personalities" in html
