"""The free sources ARE the corpus, so being refused by them is total loss.

From a live run, with the paid social tier switched off:

    news: returned nothing — HTTPError: 429 Too Many Requests for url:
    https://api.gdeltproject.org/api/v2/doc/doc?query="Kalonzo Musyoka"...

One query. The volume was not the problem: GDELT meters per IP at roughly a
request every five seconds, and this whole service is one IP shared by the
report path, the issue map, the six-hourly scheduler and the ten-minute
keep-alive ping. Nothing enforced a gap, so the news backbone returned
nothing and the corpus fell back to YouTube video titles.

Retry backs off AFTER a rejection. Pacing stops the rejection.
"""

import time

import pytest

from engine.ingestion import http


@pytest.fixture(autouse=True)
def _clean_pacing():
    http.reset_pacing()
    yield
    http.reset_pacing()


def test_a_metered_host_gets_a_gap_between_requests(monkeypatch):
    monkeypatch.setitem(http.HOST_MIN_INTERVAL, "api.gdeltproject.org", 0.3)
    started = time.monotonic()
    for _ in range(3):
        http._pace("api.gdeltproject.org")
    elapsed = time.monotonic() - started
    assert elapsed >= 0.6, f"three requests took {elapsed:.2f}s — no gap held"


def test_an_unlisted_host_is_not_slowed_down():
    """Most hosts need no pacing, and pacing them only makes a run slower."""
    started = time.monotonic()
    for _ in range(5):
        http._pace("example.com")
    assert time.monotonic() - started < 0.2


def test_being_refused_widens_the_gap_for_the_rest_of_the_run():
    """Asking again at the pace that was just refused walks straight back into
    the limit."""
    before = http._interval_for("api.gdeltproject.org")
    http._widen("api.gdeltproject.org")
    assert http._interval_for("api.gdeltproject.org") > before


def test_the_learned_gap_never_grows_without_bound():
    for _ in range(20):
        http._widen("api.gdeltproject.org")
    assert http._interval_for("api.gdeltproject.org") <= (
        http.HOST_MIN_INTERVAL["api.gdeltproject.org"] + http._LEARNED_CEILING)


def test_a_429_response_widens_the_gap(monkeypatch):
    """The widening has to happen on the real response path, not only when a
    test calls _widen directly."""
    class _Resp:
        status_code = 429

    monkeypatch.setattr(http, "_shared_session", type("S", (), {"get": lambda *a, **k: _Resp()})())
    monkeypatch.setitem(http.HOST_MIN_INTERVAL, "api.gdeltproject.org", 0.0)
    before = http._interval_for("api.gdeltproject.org")
    http.get("https://api.gdeltproject.org/api/v2/doc/doc")
    assert http._interval_for("api.gdeltproject.org") > before


def test_gdelt_is_paced_at_its_documented_limit():
    """GDELT publishes ~1 request / 5s per IP. Guessing lower is how the 429
    happened; this pins the number to the documented one."""
    assert http.HOST_MIN_INTERVAL["api.gdeltproject.org"] >= 5.0


def test_the_retry_ladder_outlasts_a_rate_limit_window():
    """At backoff_factor 1.0 the four retries spanned fourteen seconds — all
    of them inside the same blocked window, so the call failed having never
    really retried."""
    session = http.build_session()
    retry = session.get_adapter("https://x/").max_retries
    assert retry.backoff_factor >= 2.0


def test_what_was_throttled_can_be_reported(monkeypatch):
    """A thin run should be able to say "the source was throttled" rather than
    "nothing was found" — opposite findings."""
    http._widen("api.gdeltproject.org")
    assert "api.gdeltproject.org" in http.pacing_state()
