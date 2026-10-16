"""The primary record: what the state actually published, not what was reported.

A live report's biggest storyline was "Ruto board appointments", built from
sixty-nine news items about appointments to EPRA, LAPSSET and the TSC. The
authoritative record of every one of those appointments is a notice in the
**Kenya Gazette**. The corpus did not contain a single one. The same report
carried the headline "High Court rules presidential advisory offices and
appointments unconstitutional" — and not the judgment.

That is the gap behind "this report is too short". It is not volume. Four
keyword sources asked more questions return more *coverage of events*; the
documents those events are reported FROM were absent entirely, and no budget
on a news connector reaches them.

It matters commercially, not just editorially. "The Standard says he
appointed X" is journalism. "Gazette Notice No. 1234 of 3 October appointed
X" is evidence, and evidence is what a client is paying for — it is the
difference between a press summary they could have assembled themselves and
an intelligence product.

## What this reads

Kenya Law (kenyalaw.org) publishes both, free and without a key:

  - **Case law** — judgments and rulings of the superior courts.
  - **The Kenya Gazette** — appointments, revocations, commissions of
    inquiry, company notices, land transactions.

## Honesty about what has and has not been verified

This module was written without network access to kenyalaw.org, so the
request shapes below are the documented public ones and the PARSING is
deliberately structural rather than selector-based: it reads anchors and
their surrounding text, instead of depending on a CSS class this author
never saw. A site redesign changes classes constantly and changes "pages
have links" never.

That is a weaker parser on purpose, and it has one hard requirement, which
is this repository's founding rule: when it finds nothing it must say WHICH
nothing. A refused connection, a 200 that parsed to zero rows, and a search
that genuinely has no results are three different findings, and
`last_error` distinguishes them — because "no court record for this person"
is a real and publishable fact about a subject, and reporting it when the
truth is "the parser broke" is the worst thing this file could do.
"""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin

from engine.ingestion import fetch_backend, http
from engine.ingestion.base import IngestedMention, IngestionConnector

BASE = "https://new.kenyalaw.org"

#: Case law search. The public search endpoint takes a free-text query.
CASELAW_URL = BASE + "/search/?q={query}&page=1"

#: The Gazette is indexed alongside case law and filtered by collection.
GAZETTE_URL = BASE + "/search/?q={query}&doc_type=Gazette&page=1"

#: Per request. These documents are long; the corpus limit downstream is what
#: bounds the run, not this.
MAX_ITEMS = 40

#: A link worth following looks like a document, not like navigation. Kept as
#: a path test rather than a class test for the reason in the module
#: docstring.
_DOC_PATH = re.compile(r"/(judgment|akn|gazette|legislation)/", re.I)

#: Dates as these documents carry them: "3 October 2026", "2026-10-03".
_DATE_PATTERNS = (
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "%Y-%m-%d"),
    (re.compile(r"\b(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\b"), "%d %B %Y"),
)


class _Links(HTMLParser):
    """Anchors, with the text inside them.

    `html.parser` rather than a dependency: this has to keep working on the
    day nobody is watching it, and the stdlib does not get uninstalled.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href, self._text = href, []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            if text:
                self.links.append((self._href, text))
            self._href, self._text = None, []


def _parse_date(text: str) -> datetime | None:
    for pattern, fmt in _DATE_PATTERNS:
        found = pattern.search(text)
        if not found:
            continue
        try:
            return datetime.strptime(found.group(1) if fmt == "%d %B %Y"
                                     else found.group(0), fmt)
        except ValueError:
            continue
    return None


class _KenyaLawBase(IngestionConnector):
    """Shared fetch. The two subclasses differ only in what they ask for."""

    url_template = CASELAW_URL
    kind = "judgment"
    platform = "kenyalaw"

    def fetch(self, politician_name: str, aliases: list[str],
              window_start: datetime, window_end: datetime,
              query: str | None = None) -> list[IngestedMention]:
        self.last_error = None
        term = (query or politician_name or "").strip()
        if not term:
            self.last_error = "no search term"
            return []

        url = self.url_template.format(query=quote_plus(term))
        # Through the tiered fetcher, not bare `http.get`. kenyalaw.org
        # answered every search from Railway with "403 Forbidden" — and the
        # same request, python-requests fingerprint and all, gets a 200 from
        # GitHub's datacenter network (source-probe.yml, 16 Oct 2026). So the
        # refusal is about Railway's ADDRESS, not the client, and no header
        # here is known to fix it. The browser-fingerprint tier is a second
        # attempt that costs one request; refused too, it is remembered.
        result = fetch_backend.fetch_html(url, timeout=30)
        if not result.ok:
            # The host refused us. NOT "this subject has no court record" —
            # confusing those two is how a report states, in print, that a
            # person has never been to court.
            reason = http.refused(url)
            if not reason and result.status in (401, 403):
                reason = (f"Kenya Law refused this server (HTTP {result.status}) "
                          "even with a browser fingerprint")
                # Every other query this run, and the next few runs, would
                # get the same answer. Ask again in a few hours.
                http.remember_refusal(url, reason)
            self.last_error = (reason or (f"no page: {result.error}" if result.error else
                               f"no page: HTTP {result.status} via {result.backend}"))[:200]
            return []
        body = result.html

        parser = _Links()
        try:
            parser.feed(body)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"could not parse the results page: {exc}"[:200]
            return []

        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        for href, text in parser.links:
            if len(mentions) >= MAX_ITEMS:
                break
            if not _DOC_PATH.search(href) or len(text) < 12:
                continue
            link = urljoin(BASE, href)
            if link in seen:
                continue
            seen.add(link)
            # An unparseable date is NOT a date. Falling back to the
            # window end keeps ordering and windowing working, and it
            # also stamps every undated item onto one day — so the
            # volume chart drew a spike that never happened and the
            # peak-day figure counted it. Say which it is; the
            # consumer decides what to do with an unknown.
            _parsed = _parse_date(text)
            posted = _parsed or window_end
            # A judgment from 1998 is a real finding about a subject and is
            # NOT in this report's window. Clamping it to the window would
            # date it to this month, which is a false fact; it is carried
            # with its own date and the window filter downstream decides.
            mentions.append(
                IngestedMention(
                    platform=self.platform,
                    source_type=self.kind,
                    author_handle="kenyalaw.org",
                    text=text,
                    posted_at=posted,
                    engagement={},
                    raw_payload={
                        "date_estimated": not _parsed,
                        "url": link,
                        "title": text,
                        "outlet": "Kenya Law",
                        "source": f"kenyalaw_{self.kind}",
                        # Primary record, and the page should be able to say
                        # so: a gazette notice and a tweet are not the same
                        # kind of evidence and must not be weighed alike.
                        "record": "primary",
                    },
                )
            )

        if not mentions and not self.last_error:
            # A 200 that parsed to nothing. This is the case the founding
            # rule is about: it is either a genuinely empty search or a
            # parser that has stopped matching the site, and the caller
            # cannot tell from an empty list.
            self.last_error = (
                f"kenyalaw returned a page ({len(body)} bytes) but no document "
                f"links were found in it for {term!r}. Either this subject has "
                "no record in this collection, or the site's markup changed "
                "and this connector's link test no longer matches."
            )
        return mentions


class KenyaLawConnector(_KenyaLawBase):
    """Judgments and rulings."""

    url_template = CASELAW_URL
    kind = "judgment"


class KenyaGazetteConnector(_KenyaLawBase):
    """Gazette notices — appointments, revocations, commissions, land."""

    url_template = GAZETTE_URL
    kind = "gazette"
