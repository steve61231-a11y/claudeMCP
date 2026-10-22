"""No endpoint answers about a subject without the API key.

`/api/baraza` was "intentionally unauthenticated": a weekly leaderboard of
every subject in the database, with mention counts and sentiment, served to
anyone who found the URL. The subjects in this database are the people a
client is researching — for a political-intelligence product, who is being
looked into is itself the confidential fact. It was removed on 22 October
2026 with the Network tab it belonged beside.

Pinned as a rule over every route rather than as the absence of one path,
because the next public endpoint will have a different name.
"""

import inspect

from engine import api_server

#: Routes that must answer without a key, and why each is safe.
PUBLIC = {
    "/": "the page itself; it holds no data until a key is entered",
    "/api/health": "reports whether credentials are PRESENT, never their values or any subject",
}
#: FastAPI's own schema pages. They describe routes, not data.
FRAMEWORK = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_every_route_that_reads_or_writes_data_requires_the_key():
    open_routes = []
    for route in api_server.app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or path in PUBLIC or path in FRAMEWORK:
            continue
        if "_require_api_key(" not in inspect.getsource(endpoint):
            open_routes.append(path)
    assert not open_routes, f"answers without the API key: {open_routes}"


def test_the_leaderboard_is_gone():
    from fastapi.testclient import TestClient

    assert TestClient(api_server.app).get("/api/baraza").status_code == 404
