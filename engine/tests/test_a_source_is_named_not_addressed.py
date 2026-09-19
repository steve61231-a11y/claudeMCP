"""What a citation looks like to the person reading it.

Three separate things on the page rendered a source as something no reader
can use, and all three shipped in one 66-page PDF:

1. Under every quote: "ref 37d358e4". That is the first eight characters of a
   row id in our own database. Not a model leak — this template wrote it. In
   a document handed to an executive it reads like debug output that escaped.

2. In evidence rows and verification panels: the full URL as running text,
   `word-break:break-all`, wrapping across three lines.

3. Worst, in print: a CSS rule stamped `content: " (" attr(href) ")"` after
   EVERY inline citation, so an executive brief citing eight sources carried
   eight 100+ character tracking URLs through the middle of its sentences.
   That rule was added in good faith — "a citation is worthless on paper
   unless the address is on the paper" — but nobody has ever typed a URL off
   a printed page, and it destroyed the one section most likely to be read.

The rule all three violate: **a source is named, not addressed.** The outlet
is the part that carries meaning to a human; the id is for the database and
the URL is for the href. So these tests pin the absence of the anti-patterns
rather than the presence of any particular markup — the page is one file that
gets edited constantly, and the next quote template will be written by
someone who never saw this.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[2] / "web" / "pulse_app.html"
SOURCE = PAGE.read_text()


def test_no_template_prints_our_internal_ref_id():
    """`ref ${esc(q.ref)}` in a rendered string, in any spelling."""
    offenders = re.findall(r"ref \$\{[^}]*\bref\b[^}]*\}", SOURCE)
    assert not offenders, (
        f"a template still prints the raw ref id: {offenders} — "
        "use sourceLine(ref), which names the outlet")


def test_print_does_not_stamp_the_whole_address_after_a_citation():
    assert "attr(href)" not in SOURCE, (
        "print CSS is stamping the full URL after every citation again; "
        'use attr(data-src), which carries the outlet')


def test_no_url_is_rendered_as_its_own_link_text():
    """`>${esc(x.url)}<` — the address used as the words of the link."""
    offenders = re.findall(r">\$\{esc\((?:\w+\.)?url\)\}", SOURCE)
    assert not offenders, (
        f"a URL is being printed as link text: {offenders} — "
        "use outletOf(url)")


def test_no_url_is_concatenated_into_an_attribution_line():
    offenders = re.findall(r"' · '\+esc\((?:\w+\.)?url\)", SOURCE)
    assert not offenders, f"a raw URL is being appended to an attribution: {offenders}"


def test_every_render_entry_point_is_given_the_index():
    """`sourceLine` reads a module-level index. An entry point that forgets to
    set it renders the PREVIOUS report's sources, or none — and the PDF
    exporter calls these directly, so it is not enough for renderReport alone
    to do it."""
    for fn in ("renderReport", "renderIssue", "renderDeepRead"):
        body = SOURCE.split(f"function {fn}(", 1)[1][:200]
        assert "useRefIndex(" in body, f"{fn} never sets the ref index"


def test_the_server_ships_the_index_the_page_needs():
    """The page cannot name an outlet the payload does not carry. These two
    halves were written together and can only be broken together."""
    from engine.reports import citations
    out = citations.linkify_report(
        {"public_voice": {"supportive": [{"quotes": [{"ref": "abcd1234", "text": "q"}]}]}},
        [{"id": "abcd1234", "platform": "nation.africa",
          "raw_payload": {"url": "https://nation.africa/a"}}])
    assert out["ref_index"]["abcd1234"]["url"] == "https://nation.africa/a"


# --- what the helpers actually produce ---------------------------------------

def _extract(script: str, name: str) -> str:
    """Lift one declaration out of the page's IIFE, balanced-bracket exact."""
    start = script.index(name)
    is_fn = name.startswith("function")
    close = {")": "(", "}": "{", "]": "["}
    depth = {"(": 0, "{": 0, "[": 0}
    opened = False
    for i in range(start, len(script)):
        ch = script[i]
        if ch in "({[":
            depth[ch] += 1
            opened = True
        elif ch in ")}]":
            depth[close[ch]] -= 1
            if is_fn and ch == "}" and opened and not any(depth.values()):
                return script[start:i + 1]
        elif ch == ";" and not any(depth.values()):
            return script[start:i + 1]
    raise AssertionError(f"could not find the end of {name!r}")


def _run_in_node(calls: list[str]):
    """Evaluate the page's own helper source, so these assertions are about
    the shipped code rather than a paraphrase of it."""
    script = SOURCE.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    # The helpers live inside the page's IIFE; lift the two under test out of
    # it by name, along with the `esc` they depend on.
    wanted = [_extract(script, name) for name in
              ("const esc =", "function outletOf(", "function sourceLine(")]
    harness = ("let REF_INDEX = {};\n" + "\n".join(wanted)
               + "\nREF_INDEX = " + json.dumps({
                   "abcd1234": {"url": "https://www.nation.africa/kenya/news/story"
                                       "-that-goes-on?utm_source=x&utm_campaign=y",
                                "platform": "news", "posted_at": "2026-03-12T09:00:00"},
                   "noturl00": {"url": None, "platform": "tiktok", "posted_at": None},
               }) + ";\n"
               + "console.log(JSON.stringify([" + ",".join(calls) + "]));")
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _visible(html: str) -> str:
    """What a reader sees. Assertions about an attribution must be made
    against this and not the markup — the address legitimately appears in the
    href, and it is its presence in the TEXT that was the defect."""
    return re.sub(r"<[^>]+>", "", html)


requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed here")


@requires_node
def test_an_outlet_is_extracted_from_any_address():
    got = _run_in_node([
        'outletOf("https://www.nation.africa/kenya/news/a?b=c")',
        'outletOf("http://standardmedia.co.ke/x")',
        'outletOf("not a url at all")',
        'outletOf(null)',
    ])
    assert got == ["nation.africa", "standardmedia.co.ke", "not a url at all", ""]


@requires_node
def test_a_quote_is_attributed_to_its_outlet_and_date():
    line, = _run_in_node(['sourceLine("abcd1234")'])
    seen = _visible(line)
    assert seen.strip() == "nation.africa · 2026-03-12 ↗", seen
    assert "abcd1234" not in seen, "the internal id reached the page again"
    assert "utm_source" not in seen, "the address belongs in the href, not the text"
    assert 'href="https://www.nation.africa' in line, "the link must still work"


@requires_node
def test_a_stance_is_kept_in_front_of_the_attribution():
    line, = _visible(_run_in_node(['sourceLine("abcd1234", "critical")'])[0]),
    assert line.strip() == "critical · nation.africa · 2026-03-12 ↗", line


@requires_node
def test_an_untraceable_quote_does_not_look_like_a_traced_one():
    """The founding rule, applied to a citation: a source that could not be
    resolved must not render identically to one that was."""
    line, = _run_in_node(['sourceLine("nothing-here")'])
    assert "source not traced" in line
    assert "<a " not in line, "an unresolvable ref must not render as a link"


@requires_node
def test_a_ref_with_a_platform_but_no_url_still_names_something():
    line, = _run_in_node(['sourceLine("noturl00")'])
    assert "tiktok" in line and "<a " not in line
