"""The page the server sends must be the page we edit.

There were two frontends: `web/index.html`, which this route served, and
`web/pulse_app.html`, which every edit went to. They drifted silently for an
entire development cycle — the backend shipped new behaviour on each deploy
and the page never changed, so the app contradicted its own API and every
symptom read as a backend fault.

Nothing about that was visible from either file. These tests make it visible.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from engine import api_server

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def test_there_is_exactly_one_frontend():
    """A second .html here is the bug returning: whichever one is not served
    becomes a copy of the app that nobody is testing and everybody is editing.
    """
    pages = sorted(p.name for p in WEB_DIR.glob("*.html"))
    assert pages == ["pulse_app.html"], (
        f"expected one frontend, found {pages} — a second copy will drift from "
        "the served one exactly as index.html did"
    )


def test_the_served_file_is_the_one_that_exists():
    assert api_server.FRONTEND_HTML.exists()
    assert api_server.FRONTEND_HTML.name == "pulse_app.html"


def test_the_root_route_serves_the_real_app():
    client = TestClient(api_server.app)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # Markers from the work that never reached the served page.
    assert "renderDeepRead" in body, "the served page is missing the deep-read sections"
    assert "progressCard" in body, "the served page cannot show streaming progress"
    assert "fetchProgress" in body, "the served page cannot resume a run"


def test_the_page_is_revalidated_rather_than_cached_forever():
    """A page cached from before the last deploy disagrees with the API it is
    talking to, and every symptom of that looks like a backend bug."""
    client = TestClient(api_server.app)
    cache_control = client.get("/").headers.get("cache-control", "")
    assert "no-cache" in cache_control, f"got {cache_control!r}"
