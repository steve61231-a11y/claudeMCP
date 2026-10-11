"""The page got a 401 and explained what a 404 means.

A live report failed with `HTTP 401 @ .../api/report` and the paragraph
underneath said: "API base is … If that's not your Mũũgĩ engine (e.g. it's a
static host with no backend), open Settings and set it to your engine URL …
A 404 usually means the request hit something that isn't the engine."

Every word of that is true about a 404 and none of it is the problem. A 401
means the opposite of a wrong base URL: the engine IS up, IS reachable, and
answered — it rejected the key. An operator reading that paragraph goes and
changes a setting that was already correct, which is the third time this
repository has sent someone to fix the wrong thing.

The status was already on the error as `httpStatus`. The paragraph simply
was not reading it. That is this codebase's standing rule reached through an
error message: never state a cause you have not checked — and here the
status IS the check, sitting in the object being rendered.
"""

import re

import pytest

from engine.api_server import render_frontend_document

PAGE = render_frontend_document()


def _cause() -> str:
    start = PAGE.index("function httpCause(err){")
    return PAGE[start:PAGE.index("\n  }\n", start)]


def test_the_paragraph_reads_the_status_it_was_given():
    body = _cause()
    assert "err.httpStatus" in body, (
        "the explanation does not look at the status, so it can only ever "
        "describe one failure")


@pytest.mark.parametrize("status,must_mention", [
    (401, ["PULSE_API_KEY", "Settings"]),
    (403, ["PULSE_API_KEY"]),
    (404, ["engine URL"]),
    (429, ["rate-limit"]),
    (402, ["credit"]),
    (503, ["Uvicorn running"]),
])
def test_each_status_gets_its_own_remedy(status, must_mention):
    body = _cause()
    assert str(status) in body, f"{status} is not handled at all"
    for phrase in must_mention:
        assert phrase in body, (
            f"the {status} branch does not name {phrase!r}, so the reader is "
            "told what is wrong and not what to do")


def test_a_rejected_key_is_not_blamed_on_the_base_url():
    """The specific regression. A 401 proves the base URL is right — the
    engine answered — so telling the operator to change it is advice that
    can only waste their evening."""
    body = _cause()
    branch = body[body.index("status===401"):]
    branch = branch[:branch.index("if(status===404")]
    assert "up and reachable" in branch, (
        "the 401 branch does not say the engine is fine, which is the one "
        "thing that stops the reader changing the URL")
    assert "API base is" not in branch, (
        "the 401 branch still leads with the base URL")


def test_an_unknown_status_sends_the_reader_somewhere_that_answers():
    body = _cause()
    tail = body[body.rindex("return `"):]
    assert "/api/health" in tail, (
        "an unrecognised failure gives no next step; health reports the "
        "database, the model backend and whether the migration ran")


def test_the_old_one_size_paragraph_is_gone():
    assert PAGE.count("A 404 usually means the request hit something") == 1, (
        "the 404 text is still being printed for every status")
