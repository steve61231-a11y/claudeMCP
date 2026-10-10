"""Cutting prose up without losing any of it.

Three places in this codebase split analyst prose into sentences and each
did it differently. The frontend's version lost words outright: it used

    text.match(/[^.!?]+[.!?]+(?=\\s|$)/g)

and at "A levy of 2.75% is deducted monthly" nothing matches starting at
"A", because the only full stop is followed by a digit rather than a space.
The engine advances the start position a character at a time until
something does match, `match` returns what matched and discards the gaps,
and the page rendered "75% is deducted monthly".

The style checker's version does not lose words but splits inside numbers:
"Sh4.8 trillion" becomes two sentences, one of them four characters long.
That matters because the checker measures sentence length and looks for an
accusation and its attribution *within one sentence* — a split through the
middle of a figure can put the allegation in one fragment and the "police
said" that attributes it in the next, and the check passes a sentence it
should have flagged.

So: one rule, stated once.

A sentence boundary is a terminator, then whitespace, then something that
starts a sentence. That single test handles "2.75%", "nation.africa",
"No. 3" and "e.g. the" with no list of exceptions to maintain. And it is
written as a SPLIT rather than a search, so the pieces are the whole string
by construction — which is the property the tests assert, because a summary
quietly shortened is indistinguishable from a model that wrote less.

`web/pulse_app.html` carries the same rule in JavaScript, with the same
reasoning, for the prose it lays out in the browser.
"""

from __future__ import annotations

import re

#: What ends a sentence, and what may trail it.
#:
#: The boundary test is: a run of terminators, then any closing quote or
#: bracket, then whitespace, then something that starts a sentence. That one
#: test handles "2.75%", "nation.africa", "No. 3" and "e.g. the" with no
#: list of exceptions to maintain.
_TERMINATORS = ".!?"
_CLOSERS = "\"'\u2019\u201d)]"

#: A digit is deliberately NOT here. Allowing one split "See No. 3 on the
#: schedule" after the abbreviation, and a sentence that genuinely begins
#: with a bare numeral is rarer than an abbreviation followed by one. The
#: cost of being wrong this way is a longer sentence; the cost of being
#: wrong the other way is a four-character one.
_SENTENCE_START = re.compile(r"[A-Z\"'\u201c\u2018(\[]")

#: Where a one-line name is allowed to end.
LABEL_CHARS = 72


def sentences(text: str) -> list[str]:
    """The sentences in `text`, which rejoined are `text`.

    Deliberately not a linguistic sentence splitter. Small enough that
    anyone reading a surprising split can see exactly why it happened, and
    structurally incapable of dropping anything, which is the property that
    matters.

    Written as an explicit scan rather than `re.split` so that a closing
    quote stays with the sentence it closes. A regex would have to put the
    quote in the separator, where it is thrown away, or at the head of the
    next sentence, where it does not belong — and Python will not accept a
    variable-width lookbehind that would allow the third option.
    """
    body = (text or "").strip()
    if not body:
        return []

    out: list[str] = []
    start = index = 0
    size = len(body)
    while index < size:
        if body[index] not in _TERMINATORS:
            index += 1
            continue
        end = index
        while end + 1 < size and body[end + 1] in _TERMINATORS:
            end += 1
        while end + 1 < size and body[end + 1] in _CLOSERS:
            end += 1
        gap = re.match(r"\s+", body[end + 1:])
        if not gap:
            index = end + 1
            continue
        resume = end + 1 + len(gap.group(0))
        if resume >= size or not _SENTENCE_START.match(body[resume]):
            index = end + 1
            continue
        out.append(body[start:end + 1])
        start = index = resume
    if start < size:
        out.append(body[start:])
    return [part.strip() for part in out if part.strip()]


def first_sentence(text: str) -> str:
    """The opening sentence — an analyst's finding, before the working."""
    found = sentences(text)
    return found[0] if found else ""


def short_label(text: str, limit: int = LABEL_CHARS) -> str:
    """A one-line name for a piece of prose.

    Cut at a clause first: a comma or a dash is the writer's own mark for
    where the point ends. At a word boundary otherwise, never mid-word, and
    never mid-sentence without saying so with an ellipsis.
    """
    body = " ".join((first_sentence(text) or text or "").split())
    if not body:
        return ""
    # Punctuation first: a comma or a dash is the writer's own mark for
    # where the point ends. Then a subordinating conjunction, because a
    # long first sentence with no punctuation in it still has a main
    # clause, and "The levy is read as a tax" is a better name for an
    # action than "The levy is read as a tax because deduction is visib…".
    for mark in (" — ", " – ", ", ", "; ", ": ",
                 " because ", " which ", " so that ", " while ", " after ",
                 " where ", " since "):
        head = body.split(mark)[0]
        if 16 <= len(head) <= limit:
            return head.rstrip(".")
    if len(body) <= limit:
        return body.rstrip(".")
    cut = body[:limit].rsplit(" ", 1)[0]
    return (cut or body[:limit]).rstrip(",;:- ") + "…"
