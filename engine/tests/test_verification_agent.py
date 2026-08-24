"""Verification agent — nothing is asserted as fact on the model's word alone.

A fluent, confident, wrong sentence is the most damaging thing this system could
produce, because it gets acted on. These tests pin the guarantees that prevent
it: claims are checked against stored evidence, unsupported ones are labelled
rather than passed through, a fabrication with no corroboration cannot come back
"verified", and confidence tracks INDEPENDENT corroboration rather than the
model's self-belief.
"""

from datetime import datetime

from engine.agents import evidence as evidence_store
from engine.agents import verify
from engine.agents.verify import CONTRADICTED, UNVERIFIED, VERIFIED, _confidence, adjudicate
from engine.db.models import Claim, ClaimEvidence, Document, Politician


def _subject(db_session, name="Verify Probe"):
    p = Politician(name=name, aliases=[], titles=[], keywords=[], swahili_terms=[])
    db_session.add(p)
    db_session.flush()
    return p


def _doc(db_session, subject, title, body, h, domain="news.example"):
    d = Document(
        politician_id=subject.id, title=title, body=body, content_hash=h,
        domain=domain, url=f"https://{domain}/{h}", source="searxng",
        fetched_at=datetime.utcnow(),
    )
    db_session.add(d)
    return d


# --- retrieval -------------------------------------------------------------

def test_retrieval_finds_the_passage_that_bears_on_a_claim(db_session):
    subject = _subject(db_session)
    _doc(db_session, subject, "Tender",
         "The ministry awarded the Mombasa terminal tender in March 2026.", "r1")
    _doc(db_session, subject, "Sports", "The county football league resumed play.", "r2")
    db_session.commit()

    found = evidence_store.retrieve_for_claim(
        db_session, subject.id, "The Mombasa terminal tender was awarded in March 2026."
    )

    assert found, "relevant evidence must be retrievable"
    assert "mombasa" in found[0]["passage"].lower()


def test_off_topic_documents_are_never_used_as_evidence(db_session):
    """Evidence the gate rejected must not sneak back in as grounding."""
    subject = _subject(db_session)
    doc = _doc(db_session, subject, "Wrong subject",
               "A different Mombasa terminal concession entirely.", "r3")
    doc.relevance_verdict = "off_topic"
    db_session.commit()

    found = evidence_store.retrieve_for_claim(db_session, subject.id, "Mombasa terminal concession")
    assert found == []


def test_independent_sources_counts_distinct_outlets_not_copies():
    """Ten reprints of one wire story are one source, not ten."""
    repeats = [{"source": "wire.example", "url": "https://wire.example/1"} for _ in range(10)]
    assert evidence_store.independent_source_count(repeats) == 1

    varied = [
        {"source": "nation.example", "url": "https://nation.example/a"},
        {"source": "standard.example", "url": "https://standard.example/b"},
    ]
    assert evidence_store.independent_source_count(varied) == 2


# --- adjudication ----------------------------------------------------------

def test_claim_with_no_evidence_is_unverified_without_calling_the_model(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("must not spend a call when there is nothing to judge")

    monkeypatch.setattr(verify.llm, "call_json_untrusted", fail)
    outcome = adjudicate("Some unsupported assertion.", [])

    assert outcome["verdict"] == UNVERIFIED
    assert "no supporting evidence" in outcome["reason"]


def test_judge_failure_never_upgrades_a_claim(monkeypatch):
    """If the judge itself breaks, the claim must not become 'verified'."""
    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(verify.llm, "call_json_untrusted", boom)
    outcome = adjudicate("A claim.", [{"source": "x", "passage": "text"}])

    assert outcome["verdict"] == UNVERIFIED


def test_unknown_verdict_falls_back_to_unverified(monkeypatch):
    monkeypatch.setattr(
        verify.llm, "call_json_untrusted",
        lambda *a, **k: {"verdict": "probably true", "support": [1]},
    )
    outcome = adjudicate("A claim.", [{"source": "x", "passage": "text"}])
    assert outcome["verdict"] == UNVERIFIED


def test_confidence_is_driven_by_independent_corroboration():
    assert _confidence(VERIFIED, 3) > _confidence(VERIFIED, 1)
    assert _confidence(VERIFIED, 1) > _confidence(UNVERIFIED, 5)
    assert _confidence(CONTRADICTED, 5) < _confidence(UNVERIFIED, 0)


# --- end to end ------------------------------------------------------------

def test_fabricated_claim_is_caught_and_labelled(db_session, monkeypatch):
    """The core guarantee: an invented statement cannot pass as fact."""
    subject = _subject(db_session)
    _doc(db_session, subject, "Budget",
         "Verify Probe presented the national budget to parliament in June.", "e1")
    db_session.commit()

    monkeypatch.setattr(
        verify, "extract_claims_batch",
        lambda passages: {1: [
            "Verify Probe presented the national budget to parliament in June.",
            "Verify Probe was convicted of fraud in Luxembourg in 2011.",  # fabricated
        ]},
    )

    def judge(instructions, untrusted, expected_keys, max_tokens, max_untrusted_chars, model=None):
        # A faithful judge: verifies only when the retrieved passages actually
        # contain the claim's substance. The fabricated conviction is nowhere in
        # the corpus, so it cannot be supported. Claims arrive numbered in one
        # batch, so the verdict has to carry the number back.
        verdicts = []
        for line in instructions.splitlines():
            if not line.startswith("Claim ["):
                continue
            position = int(line.split("[")[1].split("]")[0])
            lowered = line.lower()
            if "luxembourg" in lowered or "convicted" in lowered:
                verdicts.append({"i": position, "verdict": UNVERIFIED, "support": [],
                                 "reason": "evidence does not mention this"})
            else:
                verdicts.append({"i": position, "verdict": VERIFIED, "support": [1],
                                 "reason": "stated in the article"})
        return {"verdicts": verdicts}

    monkeypatch.setattr(verify.llm, "call_json_untrusted", judge)

    result = verify.verify_payload(
        db_session, subject, {"executive_brief": "irrelevant — extraction is stubbed"}
    )

    assert result["checked"] == 2
    assert result["verified"] == 1
    assert result["unverified"] == 1

    statuses = {c["text"][:20]: c["status"] for c in result["claims"]}
    fabricated = next(c for c in result["claims"] if "Luxembourg" in c["text"])
    assert fabricated["status"] == UNVERIFIED
    assert fabricated["citations"] == [], "an unsupported claim must carry no citations"


def test_verdicts_and_citations_are_persisted(db_session, monkeypatch):
    """Verdicts must be auditable after the fact, not just returned once."""
    subject = _subject(db_session)
    _doc(db_session, subject, "Contract",
         "Verify Probe signed the water contract in Nakuru.", "e2")
    db_session.commit()

    monkeypatch.setattr(
        verify, "extract_claims_batch",
        lambda passages: {1: ["Verify Probe signed the water contract in Nakuru."]},
    )
    monkeypatch.setattr(
        verify.llm, "call_json_untrusted",
        lambda *a, **k: {"verdicts": [{"i": 1, "verdict": VERIFIED, "support": [1],
                                       "reason": "directly stated"}]},
    )

    verify.verify_payload(db_session, subject, {"summary": "text"})

    stored = db_session.query(Claim).filter_by(politician_id=subject.id).all()
    assert len(stored) == 1
    assert stored[0].status == VERIFIED
    assert stored[0].confidence >= 0.5
    citations = db_session.query(ClaimEvidence).filter_by(claim_id=stored[0].id).all()
    assert citations and citations[0].document_id is not None
    assert citations[0].quote


def test_nothing_to_check_is_not_an_error(db_session, monkeypatch):
    subject = _subject(db_session)
    monkeypatch.setattr(verify, "extract_claims_batch", lambda passages: {})
    result = verify.verify_payload(db_session, subject, {"summary": "nothing factual here"})
    assert result["checked"] == 0


# --- the audit must not cost more than the report ----------------------------

def test_extraction_and_judging_are_batched_not_per_item(db_session, monkeypatch):
    """`risks`, `opportunities` and `trends` are LISTS: every element is its own
    extraction target. Raising those sections from "3-5 items" to "6-12" tripled
    the extraction calls and multiplied the judgements downstream. On a
    serialised backend that was most of the wall-clock of a report.
    """
    subject = _subject(db_session)
    _doc(db_session, subject, "Record", "Verify Probe did the thing in June.", "batch-e1")
    db_session.commit()

    calls = {"extract": 0, "judge": 0}

    def counted_extract(prompt, max_tokens=1024, model=None):
        calls["extract"] += 1
        positions = [int(line.split("]")[0][1:]) for line in prompt.splitlines()
                     if line.startswith("[")]
        return {"claims": [{"i": p, "text": f"claim from passage {p}"} for p in positions]}

    def counted_judge(instructions, untrusted, expected_keys, max_tokens,
                      max_untrusted_chars, model=None):
        calls["judge"] += 1
        positions = [int(line.split("[")[1].split("]")[0])
                     for line in instructions.splitlines() if line.startswith("Claim [")]
        return {"verdicts": [{"i": p, "verdict": UNVERIFIED, "support": [], "reason": "x"}
                             for p in positions]}

    monkeypatch.setattr(verify.llm, "call_json", counted_extract)
    monkeypatch.setattr(verify.llm, "call_json_untrusted", counted_judge)

    payload = {
        "executive_brief": "A brief.",
        "summary": "A summary.",
        "risks": [f"Risk number {i}." for i in range(12)],
        "opportunities": [f"Opportunity number {i}." for i in range(12)],
        "trends": [f"Trend number {i}." for i in range(12)],
    }
    result = verify.verify_payload(db_session, subject, payload)

    # 38 passages. One call each would be 38; batched at 10 it is 4.
    assert result["checked"] == 38
    assert calls["extract"] == 4, f"expected 4 batched extraction calls, got {calls['extract']}"
    # 38 claims, batched at 6 for adjudication.
    assert calls["judge"] <= 7, f"expected <=7 batched judging calls, got {calls['judge']}"


def test_a_claim_with_no_evidence_never_costs_a_call(db_session, monkeypatch):
    """It is unverified by definition — paying a model to say so is waste."""
    subject = _subject(db_session)
    db_session.commit()  # no documents at all, so nothing can be retrieved

    calls = {"judge": 0}
    monkeypatch.setattr(verify, "extract_claims_batch",
                        lambda passages: {1: ["Something nothing supports."]})

    def counted(*a, **k):
        calls["judge"] += 1
        return {"verdicts": []}

    monkeypatch.setattr(verify.llm, "call_json_untrusted", counted)

    result = verify.verify_payload(db_session, subject, {"summary": "text"})
    assert result["checked"] == 1
    assert result["unverified"] == 1
    assert calls["judge"] == 0


def test_an_unanswered_claim_is_never_upgraded(db_session, monkeypatch):
    """A judge that fails or skips a claim must leave it unverified — silence
    can never be read as support."""
    subject = _subject(db_session)
    _doc(db_session, subject, "Record", "Verify Probe did the thing.", "skip-e1")
    db_session.commit()

    monkeypatch.setattr(verify, "extract_claims_batch",
                        lambda passages: {1: ["Verify Probe did the thing."]})
    monkeypatch.setattr(verify.llm, "call_json_untrusted",
                        lambda *a, **k: {"verdicts": []})  # answers for nothing

    result = verify.verify_payload(db_session, subject, {"summary": "text"})
    assert result["verified"] == 0
    assert result["unverified"] == 1


def test_a_verdict_for_a_claim_that_was_not_asked_about_is_ignored(db_session, monkeypatch):
    """Position numbers are the only thing tying a verdict to its claim; an
    out-of-range one must not land on somebody else's claim."""
    subject = _subject(db_session)
    _doc(db_session, subject, "Record", "Verify Probe did the thing.", "oor-e1")
    db_session.commit()

    monkeypatch.setattr(verify, "extract_claims_batch",
                        lambda passages: {1: ["Verify Probe did the thing."]})
    monkeypatch.setattr(
        verify.llm, "call_json_untrusted",
        lambda *a, **k: {"verdicts": [
            {"i": 99, "verdict": VERIFIED, "support": [1], "reason": "not a real claim"},
        ]},
    )
    result = verify.verify_payload(db_session, subject, {"summary": "text"})
    assert result["verified"] == 0
    assert result["unverified"] == 1
