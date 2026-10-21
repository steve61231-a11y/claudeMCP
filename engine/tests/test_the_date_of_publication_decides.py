"""Mentions going back to the last election, inside a one-month report.

An investor reading a live report: "muugi is adding mentions/posts outside of
the selected reporting period, with some going back to the last election ...
We should focus on the date of publication first and then reactions only when
they fall in the reporting period."

He was right, and it was not one bug. Every collector clamped an item's REAL
date into the window — `min(max(posted, window_start), window_end)` — so an
old article was stored as published on the first day of the month. Measured
from GitHub's network on 21 Oct 2026 for a one-month query:

  Google News, no time limit      382 of 806 predated the month
  Google News, `when:30d`           0 of 699
  YouTube flat search               0 of 40 carried any date at all —
                                    every one counted as this month's
  YouTube, dates on                14 of 40 predated it, oldest from 2018
  YouTube, "uploaded this month"  895 of 897 dated, 19 just outside it

And YouTube comments took their VIDEO's date, which is the investor's own
guess — "could this be because it's tracking reactions as opposed to posts?"

The rule now, in one place (`ingestion.base.period_date`) and one filter
(`db.period.dated`): an item counts when it was PUBLISHED in the period; a
reaction counts when IT falls in the period; an item with no known date is
kept and never counted as this period's; and the dates already moved into a
window in the store are found and marked, because each one equals its run's
window start or end exactly.
"""

from datetime import datetime, timedelta, timezone

import pytest

from engine.config import settings
from engine.db.models import Document, IngestionRun, Politician, RawMention
from engine.ingestion import google_news_rss_connector as gnews
from engine.ingestion import http
from engine.ingestion import reddit_connector as reddit
from engine.ingestion import youtube_connector as yt
from engine.ingestion.base import period_date

START = datetime(2026, 9, 21, 7, 30)
END = datetime(2026, 10, 21, 7, 30)


# --- the rule ------------------------------------------------------------------

def test_a_date_before_the_period_is_not_this_period_s():
    assert period_date(datetime(2022, 8, 9), START, END) is None


def test_a_date_in_the_period_is_kept_exactly():
    when = datetime(2026, 10, 3, 14, 5)
    assert period_date(when, START, END) == (when, False)


def test_no_date_is_kept_and_marked_never_assumed():
    assert period_date(None, START, END) == (END, True)


def test_a_timezone_is_normalised_before_comparing():
    eat = timezone(timedelta(hours=3))
    posted, estimated = period_date(datetime(2026, 10, 3, 17, 5, tzinfo=eat), START, END)
    assert posted == datetime(2026, 10, 3, 14, 5) and estimated is False


def test_no_collector_moves_a_date_any_more():
    """The clamp, anywhere in ingestion, is the bug."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "ingestion"
    offenders = [p.name for p in root.glob("*.py")
                 if "min(max(" in "\n".join(line for line in p.read_text().splitlines()
                                            if not line.lstrip().startswith(("#", "`", "-")))
                 and p.name != "base.py"]
    assert not offenders, f"still clamping dates into the window: {offenders}"


# --- Google News ------------------------------------------------------------------

class _Resp:
    def __init__(self, content=b"", json_data=None, status=200):
        self.content, self._json, self.status_code = content, json_data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Ruto meets governors</title><link>https://news.google.com/a</link>
<pubDate>Mon, 13 Oct 2026 09:00:00 GMT</pubDate>
<source url="https://nation.africa">Daily Nation</source></item>
<item><title>Election petition filed</title><link>https://news.google.com/b</link>
<pubDate>Tue, 09 Aug 2022 09:00:00 GMT</pubDate>
<source url="https://www.the-star.co.ke">The Star</source></item>
</channel></rss>"""


def test_google_news_is_asked_for_the_period_only(monkeypatch):
    asked = []

    def get(url, **kw):
        asked.append(url)
        return _Resp(RSS)

    monkeypatch.setattr(http, "get", get)
    gnews.GoogleNewsRssConnector().fetch("William Ruto", [], START, END, query='"William Ruto"')
    assert "when%3A30d" in asked[0], f"no time limit in {asked[0]}"


def test_google_news_drops_what_predates_the_period_instead_of_moving_it(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: _Resp(RSS))
    out = gnews.GoogleNewsRssConnector().fetch("William Ruto", [], START, END)
    assert [m["text"] for m in out] == ["Ruto meets governors"]
    assert out[0]["posted_at"] == datetime(2026, 10, 13, 9, 0)


def test_google_news_keeps_the_outlet_s_own_domain(monkeypatch):
    """The link is a news.google.com redirect; the outlet is in <source url>."""
    monkeypatch.setattr(http, "get", lambda url, **kw: _Resp(RSS))
    out = gnews.GoogleNewsRssConnector().fetch("William Ruto", [], START, END)
    assert out[0]["raw_payload"]["outlet_domain"] == "nation.africa"


# --- YouTube ---------------------------------------------------------------------

class _YDL:
    def __init__(self, info, seen):
        self._info, self._seen = info, seen

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, target, download=False):
        self._seen.append(target)
        return self._info


def _ts(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _youtube(monkeypatch, entries, comments=None):
    monkeypatch.setattr(settings, "low_memory", False, raising=False)
    monkeypatch.setattr(yt, "_COMMENTS_REFUSED_UNTIL", 0.0)
    seen, opts_seen = [], []

    def factory(opts):
        opts_seen.append(opts)
        return _YDL({"comments": comments or []} if opts.get("getcomments")
                    else {"entries": entries}, seen)

    out = yt.YouTubeConnector(ydl_factory=factory).fetch("William Ruto", [], START, END)
    return out, seen, opts_seen


def test_youtube_searches_this_month_s_uploads_with_their_dates(monkeypatch):
    _, seen, opts = _youtube(monkeypatch, [])
    assert "sp=" + yt.UPLOADED_THIS_MONTH in seen[0], "the search is not limited to recent uploads"
    search = opts[0]
    assert search["extractor_args"]["youtubetab"]["approximate_date"] == [""]
    assert search["playlistend"] >= yt.SEARCH_PAGE, "a filtered search paginates without a cap"


def test_youtube_drops_old_videos_and_dates_the_rest(monkeypatch):
    entries = [
        {"id": "new", "title": "Ruto in Kisumu", "timestamp": _ts(datetime(2026, 10, 12))},
        {"id": "old", "title": "2022 campaign rally", "timestamp": _ts(datetime(2022, 8, 1))},
        {"id": "nodate", "title": "Undated clip"},
    ]
    out, _, _ = _youtube(monkeypatch, entries)
    videos = {m["raw_payload"]["video_id"]: m for m in out if m["source_type"] == "video"}
    assert set(videos) == {"new", "nodate"}, "a 2022 video was collected for this month"
    assert videos["new"]["posted_at"] == datetime(2026, 10, 12)
    assert videos["new"]["raw_payload"]["date_estimated"] is False
    assert videos["nodate"]["raw_payload"]["date_estimated"] is True


def test_a_reaction_is_dated_by_itself_not_by_its_video(monkeypatch):
    entries = [{"id": "v1", "title": "Ruto in Kisumu", "timestamp": _ts(datetime(2026, 10, 12))}]
    comments = [
        {"text": "Fresh reaction", "timestamp": _ts(datetime(2026, 10, 14, 8))},
        {"text": "Reaction from before the period", "timestamp": _ts(datetime(2026, 9, 1))},
        {"text": "Reaction with no time"},
    ]
    out, _, _ = _youtube(monkeypatch, entries, comments)
    reactions = {m["text"]: m for m in out if m["source_type"] == "comment"}
    assert "Reaction from before the period" not in reactions
    assert reactions["Fresh reaction"]["posted_at"] == datetime(2026, 10, 14, 8)
    assert reactions["Reaction with no time"]["raw_payload"]["date_estimated"] is True


def test_youtube_stops_asking_for_comments_once_refused(monkeypatch):
    monkeypatch.setattr(settings, "low_memory", False, raising=False)
    monkeypatch.setattr(yt, "_COMMENTS_REFUSED_UNTIL", 0.0)
    attempts = []
    entries = [{"id": f"v{i}", "title": f"Clip {i}", "timestamp": _ts(datetime(2026, 10, 12))}
               for i in range(4)]

    class _Refusing(_YDL):
        def extract_info(self, target, download=False):
            attempts.append(target)
            raise RuntimeError("Sign in to confirm you're not a bot")

    def factory(opts):
        return _Refusing({}, []) if opts.get("getcomments") else _YDL({"entries": entries}, [])

    yt.YouTubeConnector(ydl_factory=factory).fetch("William Ruto", [], START, END)
    assert len(attempts) == 1, f"asked {len(attempts)} times after YouTube refused"


# --- Reddit ---------------------------------------------------------------------

def test_reddit_drops_old_posts_instead_of_moving_them(monkeypatch):
    monkeypatch.setattr(settings, "reddit_client_id", None)
    children = [
        {"data": {"id": "a", "title": "Ruto this week", "created_utc": _ts(datetime(2026, 10, 10))}},
        {"data": {"id": "b", "title": "Ruto in 2023", "created_utc": _ts(datetime(2023, 3, 1))}},
    ]
    monkeypatch.setattr(http, "get", lambda url, **kw: _Resp(json_data={"data": {"children": children}}))
    out = reddit.RedditConnector().fetch("William Ruto", [], START, END)
    assert {m["text"] for m in out} == {"Ruto this week"}


# --- the store ------------------------------------------------------------------

def _subject(db):
    p = Politician(name="Edwin Sifuna", aliases=[], keywords=[], titles=[], swahili_terms=[],
                   tracked_hashtags=[], social_handles={})
    db.add(p)
    db.commit()
    return p


def test_dates_already_moved_into_a_window_are_found_and_marked(db_session):
    import importlib

    from sqlalchemy import text

    repair = importlib.import_module(
        "engine.alembic.versions.b4c5d6e7f8a9_dates_moved_into_a_window")
    subject = _subject(db_session)
    run = IngestionRun(politician_id=subject.id, window_start=START, window_end=END,
                       status="completed")
    db_session.add(run)
    db_session.flush()

    def mention(text_, when, payload=None):
        m = RawMention(politician_id=subject.id, run_id=run.id, platform="news",
                       source_type="article", author_handle="x", text=text_, posted_at=when,
                       raw_payload=payload or {}, content_hash=text_, is_spam=0)
        db_session.add(m)
        return m

    clamped_old = mention("2022 article clamped forward", START)
    clamped_new = mention("undated comment stamped at the end", END)
    real = mention("published mid-period", START + timedelta(days=9, minutes=7))
    db_session.commit()

    db_session.execute(text(repair.REPAIR_SQL))
    db_session.commit()
    for row in (clamped_old, clamped_new, real):
        db_session.refresh(row)
    assert clamped_old.raw_payload["date_estimated"] is True
    assert clamped_new.raw_payload["date_estimated"] is True
    assert not real.raw_payload.get("date_estimated"), "a real date was marked as unknown"


def test_the_report_counts_only_what_was_published_in_the_period(db_session, monkeypatch):
    from engine.tests.test_end_to_end_run import WINDOW_DAYS, _scripted
    from engine import health, llm, stages
    from engine.pipeline import run_analysis

    monkeypatch.setattr(llm, "_call_json", _scripted)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "concurrency", lambda n: 1)
    monkeypatch.setattr(settings, "enable_resolution", False)
    monkeypatch.setattr(settings, "enable_verification", False)
    health.reset()
    stages.reset()
    subject = _subject(db_session)
    now = datetime.utcnow()
    for i in range(12):
        undated = i >= 8          # four items whose date nobody knows
        db_session.add(RawMention(
            politician_id=subject.id, platform="nation.africa", source_type="article",
            author_handle=f"a{i}", text=f"Edwin Sifuna spoke in Nairobi, story {i}.",
            posted_at=now - timedelta(days=i % 5), content_hash=f"h{i}", is_spam=0,
            raw_payload={"date_estimated": undated}, source_url=f"https://x.co.ke/{i}"))
    db_session.commit()

    report = run_analysis(db_session, subject, "weekly", now - timedelta(days=WINDOW_DAYS), now)
    assert report.payload["volume_trends"]["total_mentions"] == 8, (
        "undated items were counted as published in the period")


def test_documents_outside_the_period_are_not_read_for_it(db_session):
    from engine.pipeline import _document_corpus

    subject = _subject(db_session)
    for title, when in (("This month", START + timedelta(days=3)),
                        ("Last election", datetime(2022, 8, 9)),
                        ("Undated page", None)):
        db_session.add(Document(politician_id=subject.id, title=title, body="text " * 20,
                                published_at=when, url=f"https://x.co.ke/{title}"))
    db_session.commit()
    titles = [d["text"].split("\n")[0] for d in _document_corpus(db_session, subject, START, END)]
    assert titles == ["This month"]


def test_the_new_in_24h_figure_ignores_undated_items():
    import inspect

    from engine import api_server

    source = inspect.getsource(api_server)
    block = source[source.index('"mentionsLast24h"') - 900:source.index('"mentionsLast24h"')]
    assert "dated()" in block
