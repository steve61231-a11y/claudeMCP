"""The evidence section must actually carry evidence.

"Representative mentions" rendered six rows of a platform chip, a bare "@" and
no text — on every live report. The API sent `headline`; the page read `text`
and `author`, neither of which was in the payload. Nothing errored, so it
looked like there was simply nothing to show.

Three different shapes reach that renderer — the stored-mention payload, the
corpus preview's `notable_mentions`, and the demo fixture — and they disagreed
about key names. That disagreement is the bug.
"""

import re
from pathlib import Path

APP_HTML = Path(__file__).resolve().parents[2] / "web" / "pulse_app.html"
API_SERVER = Path(__file__).resolve().parents[1] / "api_server.py"


def test_the_api_sends_the_keys_the_page_reads():
    source = API_SERVER.read_text(encoding="utf-8")
    block = source[source.index("scored_mentions.append("):]
    block = block[: block.index("scored_mentions.sort")]
    for key in ('"text"', '"author"', '"url"', '"platform"'):
        assert key in block, f"topMentions is missing {key}, which the page renders"


def test_the_renderer_tolerates_every_shape_that_reaches_it():
    html = APP_HTML.read_text(encoding="utf-8")
    block = html[html.index("const tm=(r.volume"):]
    block = block[: block.index("wrap.appendChild(mc)")]
    # Author, under any of its names.
    assert "m.author||m.author_handle||m.handle" in block
    # Body text, under either of its names.
    assert "m.text||m.headline" in block
    # And an empty row must say so rather than rendering blank.
    assert "no text captured" in block


def test_the_source_count_is_not_called_platforms():
    """`platform` is the outlet for news, so 41 news domains showed as
    "41 platforms" — which reads as nonsense next to a mention count."""
    html = APP_HTML.read_text(encoding="utf-8")
    assert "' sources'" in html
    assert "+' platforms'" not in html
