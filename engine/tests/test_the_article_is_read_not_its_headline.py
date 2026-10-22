"""Google News, the largest news source, gave the corpus headlines only.

Measured on 22 October 2026 from GitHub's network (`scripts/article_probe.py`):
0 of 15 Google News links gave an article body. The link the connector stores
is `news.google.com/rss/articles/<id>` — Google's page, not the newspaper's —
so trafilatura was handed Google's page and found nothing, and every one of
those items entered the corpus as a title. Decoded to the publisher first,
15 of 15 resolved and 11 of 15 gave the full article.

And an article was identified by a hash of its whole text, so the headline
copy one run stored and the full article a later run fetched were two rows:
counted twice in every figure, read once as nothing but a title.
"""

from datetime import datetime

import pytest

from engine.config import settings
from engine.ingestion import article_text as at
from engine.ingestion import google_news_links as gnl
from engine.ingestion import orchestrator
from engine.processing import cleaning

LINK = "https://news.google.com/rss/articles/CBMiABC123xyz?oc=5"
PUBLISHER = "https://nation.africa/kenya/news/politics/senate-vote-4321"
PAGE = '<c-wiz><div jscontroller="x" data-n-a-sg="SIG-abc" data-n-a-ts="1729500000"></div></c-wiz>'
REPLY = ')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"' + PUBLISHER + '\\",1]",null,null,null,"generic"]]'


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


@pytest.fixture(autouse=True)
def _fresh():
    gnl.reset()
    yield
    gnl.reset()


def _google(monkeypatch, page=PAGE, reply=REPLY):
    calls = []
    monkeypatch.setattr(gnl.http, "get", lambda url, **kw: calls.append(("get", url)) or _Resp(page))
    monkeypatch.setattr(gnl.http, "post", lambda url, **kw: calls.append(("post", kw.get("data"))) or _Resp(reply))
    return calls


def test_a_google_news_link_resolves_to_the_newspaper(monkeypatch):
    calls = _google(monkeypatch)
    assert gnl.decode(LINK) == PUBLISHER
    assert calls[0] == ("get", "https://news.google.com/rss/articles/CBMiABC123xyz")
    assert "CBMiABC123xyz" in calls[1][1] and "SIG-abc" in calls[1][1]


def test_a_resolved_link_is_not_asked_again(monkeypatch):
    calls = _google(monkeypatch)
    gnl.decode(LINK)
    gnl.decode(LINK)
    assert len(calls) == 2, "the same story comes back from several queries in one run"


def test_any_other_link_passes_through(monkeypatch):
    calls = _google(monkeypatch)
    assert gnl.decode(PUBLISHER) == PUBLISHER
    assert not calls


@pytest.mark.parametrize("page,reply", [
    ("<html>consent page</html>", REPLY),        # no signature on the page
    (PAGE, ")]}'\n\nnot json"),                  # a reply in some other shape
])
def test_a_link_that_will_not_resolve_says_so_and_never_raises(monkeypatch, page, reply):
    _google(monkeypatch, page=page, reply=reply)
    assert gnl.decode(LINK) is None
    assert sum(gnl.outcomes().values()) == 1 and "decoded" not in gnl.outcomes()


def _gn_item(title="Senate vote on the Finance Bill"):
    return {"platform": "dailynation", "source_type": "article", "author_handle": "Daily Nation",
            "text": title, "posted_at": datetime(2026, 10, 20), "engagement": {},
            "raw_payload": {"url": LINK, "title": title, "outlet": "Daily Nation",
                            "source": "google_news_rss"}}


def test_the_article_is_read_from_the_newspaper_and_linked_there(monkeypatch):
    monkeypatch.setattr(settings, "enable_article_text", True, raising=False)
    _google(monkeypatch)
    fetched = []
    monkeypatch.setattr(at, "extract_body",
                        lambda url, mc: (fetched.append(url) or "The full article, many paragraphs.",
                                         "requests"))
    item = _gn_item()
    assert at.enrich_with_article_text([item]) == 1
    assert fetched == [PUBLISHER], "Google's page was fetched as if it were the article"
    raw = item["raw_payload"]
    assert raw["url"] == PUBLISHER and raw["google_news_url"] == LINK
    assert raw["outlet_domain"] == "nation.africa"
    assert "The full article" in item["text"]


def test_an_unresolved_item_stays_a_headline_and_is_counted(monkeypatch):
    monkeypatch.setattr(settings, "enable_article_text", True, raising=False)
    _google(monkeypatch, page="<html>no signature</html>")
    monkeypatch.setattr(at, "extract_body", lambda url, mc: pytest.fail("fetched Google's page"))
    item = _gn_item()
    assert at.enrich_with_article_text([item]) == 0
    assert item["text"] == "Senate vote on the Finance Bill"


def test_a_headline_and_its_article_are_one_item():
    headline = _gn_item()
    full = _gn_item()
    full["text"] = "Senate vote on the Finance Bill\n\nThe full article, many paragraphs."
    a, b = cleaning.clean_mentions([headline]), cleaning.clean_mentions([full])
    assert a[0]["content_hash"] == b[0]["content_hash"]


def test_a_stored_headline_is_upgraded_to_the_article_in_place(db_session, monkeypatch):
    from engine.db.models import IngestionTask, RawMention
    from engine.tests.test_orchestrator import WINDOW, make_politician

    monkeypatch.setattr(settings, "socialcrawl_api_key", "")
    monkeypatch.setattr(settings, "newsapi_key", "")
    politician = make_politician(db_session)
    run = orchestrator.plan_run(db_session, politician, *WINDOW)
    task = db_session.query(IngestionTask).filter_by(run_id=run.id).first()

    assert orchestrator._store_mentions(db_session, run, task, politician, [_gn_item()]) == 1
    full = _gn_item()
    full["text"] = "Senate vote on the Finance Bill\n\nThe full article, many paragraphs."
    full["raw_payload"] = {**full["raw_payload"], "url": PUBLISHER, "article_text": "The full article"}
    # Not new — the learning loop ranks queries on NEW mentions.
    assert orchestrator._store_mentions(db_session, run, task, politician, [full]) == 0
    rows = db_session.query(RawMention).filter_by(politician_id=politician.id).all()
    assert len(rows) == 1
    assert "many paragraphs" in rows[0].text and rows[0].source_url == PUBLISHER
    # And a later headline-only copy does not shrink it back.
    orchestrator._store_mentions(db_session, run, task, politician, [_gn_item()])
    db_session.expire_all()
    assert "many paragraphs" in db_session.query(RawMention).one().text


def test_pairs_stored_before_this_are_folded_when_a_report_reads_them():
    from types import SimpleNamespace

    from engine.pipeline import _one_row_per_article

    def row(text, title="Senate vote"):
        return SimpleNamespace(source_type="article", platform="dailynation",
                               author_handle="Daily Nation", text=text,
                               raw_payload={"title": title})

    comment = SimpleNamespace(source_type="comment", platform="youtube", author_handle="@a",
                              text="Senate vote", raw_payload={})
    rows = [row("Senate vote"), comment, row("Senate vote\n\nthe whole article"),
            row("Another story", title="Another story")]
    kept = _one_row_per_article(rows)
    assert len(kept) == 3
    assert kept[0].text.endswith("the whole article"), "the fuller copy is the one kept"
    assert kept[1] is comment
