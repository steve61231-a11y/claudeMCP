"""Whether a text is about the issue: one rule for the map's pools, its connection and its records.

An issue is usually a NAME and what is said about it: "Safaricom shares
sale" is Safaricom, and a share sale; "Mau forest evictions" is the Mau,
and evictions from its forest; "the IMF loan" is the IMF, and a loan. The
map matched on the name alone, because the name was one of the terms a
document could carry. A live run (scripts/record_probe.py, 2 Nov 2026) then
put a member's "let them emulate Safaricom" under the Safaricom share sale
and two speeches on the Rironi-Mau Summit ROAD under Mau forest evictions,
and every document naming Safaricom and the government was counted as the
intersection itself. The name is the issue's head, not the issue.

So a text is about the issue when it:

  * prints a phrase that names the issue whole — the issue as typed, an
    alias the operator gave, or what Kenyan sources call it ("housing levy"
    is the Affordable Housing Levy); or
  * names every head (an acronym or its expansion: IMF or International
    Monetary Fund) AND at least one of the other words, as a source would
    write them ("sale" is also "sold", "selling", "divest"; "evictions" is
    also "evicted"); an issue that IS its name (SHA) needs nothing more; or
  * for an issue with no name in it ("school fees", "teachers strike"),
    carries every distinctive word — and for an issue typed as a sentence,
    most of them, with at least one of its names.

The variants are a short, checked list, because a variant that is wrong
puts a document under an issue it is not about. No model reads anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from engine.reports import decompose, issue_profile

#: Words that name no particular issue, or only the country it happens in.
GENERIC = frozenset("""
case issue matter story affair deal plan policy project programme program bill act scandal row
saga the and for with from kenya kenyan kenyans county counties national government its his her
their our of to in on at by a an over into after before against about vs versus
""".split())

#: Endings that join a word to its relatives: "evict" is "evicted",
#: "evictions", "evicting". A stem matches a word that is the stem plus one
#: of these, so "fee" never matches "feed" and "protest" never "protestant".
_ENDINGS = ("", "s", "es", "e", "ed", "ing", "er", "ers", "ion", "ions", "ation", "ations", "ment", "ments",
            "al", "ary", "ry", "ive", "ting", "ted", "ning", "ned", "ping", "ped", "ies")
_SUFFIXES = ("ations", "ation", "ments", "ment", "ions", "ion", "ings", "ing", "ers", "ies", "es", "ed", "er", "s")

#: What a source writes for the word the client typed — the commonest
#: Kenyan reporting of each, nothing speculative. Keyed by stem.
VARIANTS: dict[str, tuple[str, ...]] = {
    "sale": ("sell", "sold", "divest", "divestiture", "disposal", "offload", "privatis", "privatiz"),
    "sell": ("sale", "sold", "divest", "offload"),
    "shar": ("stake", "shareholding", "equity"),
    "stake": ("share", "shareholding"),
    "loan": ("borrow", "lend", "lender", "credit", "financing", "facility", "bailout", "tranche", "debt",
             "programme", "program", "disbursement"),
    "debt": ("loan", "borrow", "eurobond", "creditor"),
    "evict": ("removal", "resettle", "demolition", "demolish", "displace"),
    "protest": ("demonstration", "demonstrator", "maandamano", "riot"),
    "strike": ("industrial action", "down tools", "downed tools", "walkout", "go-slow", "boycott"),
    "fee": ("tuition", "capitation", "levy"),
    "cut": ("slash", "reduction", "reduce", "austerity"),
    "budget": ("appropriation", "estimates", "allocation", "expenditure"),
    "tax": ("levy", "duty", "vat", "excise"),
    "levy": ("tax", "deduction"),
    "abduct": ("disappearance", "disappeared", "kidnap"),
    "killing": ("kill", "murder", "shot dead"),
    "corrupt": ("graft", "bribe", "embezzle", "kickback"),
    "corruption": ("corrupt", "graft", "bribe", "embezzle", "kickback"),
    "tender": ("procurement", "contract", "bid"),
    "contract": ("tender", "procurement", "agreement"),
    "lease": ("concession",),
    "concession": ("lease",),
    "takeover": ("acquisition", "acquire", "buyout"),
    "privatis": ("privatiz", "sale", "sell", "divest"),
    "impeach": ("removal from office",),
    "election": ("poll", "vote", "ballot"),
    "shortage": ("scarcity", "stockout", "out of stock"),
    "pay": ("paid", "arrears", "pending bills", "reimburse", "disburse"),
    "delay": ("delayed", "arrears", "unpaid", "pending bills"),
    "hike": ("increase", "rise", "raise"),
}


def _tokens(text: str) -> list[str]:
    """Folded words. An apostrophe is a break, on both sides of every
    comparison: the fold glues "Adani's" into "adanis", which no name is."""
    from engine.evidence import facts

    return re.findall(r"[a-z0-9]+", facts._fold(re.sub(r"[’'‘`]", " ", text or "")))


def _stem(word: str) -> str:
    w = word.lower()
    for suffix in _SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return w


def _word_matches(stem: str, token: str) -> bool:
    if token == stem or _stem(token) == stem:
        return True
    # "strike" and "striking" share "strik"; "fee" never folds to "f".
    bare = stem[:-1] if stem.endswith("e") and len(stem) > 3 else None
    if bare and _stem(token) == bare:
        return True
    return token.startswith(stem) and token[len(stem):] in _ENDINGS


def _variants(stem: str) -> tuple[str, ...]:
    for key in (stem, stem + "e", stem[:-1] if stem.endswith("e") else None):
        if key and key in VARIANTS:
            return VARIANTS[key]
    return ()


def _has_sequence(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    if not n:
        return False
    if n == 1:
        return phrase[0] in tokens
    joined = f" {' '.join(tokens)} "
    return f" {' '.join(phrase)} " in joined


@dataclass(frozen=True)
class IssueMatch:
    issue: str
    #: Each names the issue whole, as a token sequence.
    phrases: tuple[tuple[str, ...], ...]
    #: Each head is a set of alternatives (an acronym and its expansion).
    heads: tuple[tuple[tuple[str, ...], ...], ...]
    #: Each qualifier: its stem, what sources also write for it, and the
    #: word as the client typed it.
    qualifiers: tuple[tuple[str, tuple[str, ...], str], ...]
    sentence: bool

    # ---- the rule -----------------------------------------------------------

    def _qualifier_hits(self, tokens: list[str]) -> int:
        hits = 0
        for stem, variants, _ in self.qualifiers:
            if any(_word_matches(stem, t) for t in tokens):
                hits += 1
                continue
            for variant in variants:
                parts = tuple(_tokens(variant))
                if len(parts) > 1:
                    if _has_sequence(tokens, parts):
                        hits += 1
                        break
                elif parts and any(_word_matches(_stem(parts[0]), t) for t in tokens):
                    hits += 1
                    break
        return hits

    def _heads_named(self, tokens: list[str]) -> int:
        return sum(1 for head in self.heads if any(_has_sequence(tokens, alt) for alt in head))

    def names(self, text: str) -> bool:
        tokens = _tokens(text)
        if not tokens:
            return False
        if any(_has_sequence(tokens, p) for p in self.phrases):
            return True
        heads = self._heads_named(tokens)
        if self.sentence:
            from engine.evidence import connection

            if self.heads and not heads:
                return False
            n = len(self.heads) + len(self.qualifiers)
            return n > 0 and heads + self._qualifier_hits(tokens) >= connection._needed(n)
        if self.heads:
            if heads < len(self.heads):
                return False
            return not self.qualifiers or self._qualifier_hits(tokens) >= self.qualifiers_needed
        return bool(self.qualifiers) and self._qualifier_hits(tokens) >= len(self.qualifiers)

    def head_only(self, text: str) -> bool:
        """Names the issue's names and not the issue — Safaricom, and no
        sale. Said as its own reason when a document is set aside."""
        tokens = _tokens(text)
        return bool(self.heads) and self._heads_named(tokens) == len(self.heads) and not self.names(text)

    @property
    def qualifiers_needed(self) -> int:
        n = len(self.qualifiers)
        return 1 if n <= 2 else (n + 1) // 2

    __call__ = names

    # ---- for court cards: an acronym alone may be a party's initials -------

    def acronym_only(self, text: str) -> str | None:
        """The head acronym a text names WITHOUT any of its spelled-out
        names, where one exists — in a court record "JMA v SHA" is two
        people's initials, and "SHA" there is not the Social Health
        Authority."""
        tokens = _tokens(text)
        for head in self.heads:
            short = [alt for alt in head if len(alt) == 1 and len(alt[0]) <= 5]
            long = [alt for alt in head if len(alt) > 1 or len(alt[0]) > 5]
            if short and long and any(_has_sequence(tokens, a) for a in short) \
                    and not any(_has_sequence(tokens, a) for a in long):
                return short[0][0].upper()
        return None

    def describe(self) -> list[str]:
        """What a document had to carry, in words an operator can check."""
        out = [" ".join(p) for p in self.phrases[:3]]
        if self.heads:
            names = " and ".join("/".join(" ".join(a) for a in head[:2]) for head in self.heads)
            words = " or ".join(word for _, _, word in self.qualifiers)
            out.append(f"{names} with {words}" if words else names)
        elif self.qualifiers:
            out.append(" + ".join(word for _, _, word in self.qualifiers))
        return out


def _clean(issue: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", (issue or "").strip(), flags=re.I)


@lru_cache(maxsize=256)
def _build(issue: str, aliases: tuple[str, ...]) -> IssueMatch:
    cleaned = _clean(issue)
    reverse = {e.lower(): a for a, es in issue_profile.ACRONYMS.items() for e in es}

    heads: list[tuple[tuple[str, ...], ...]] = []
    head_tokens: set[str] = set()
    for anchor in decompose.anchors(cleaned):
        alternatives = [anchor, *issue_profile.ACRONYMS.get(anchor.upper(), ())]
        if anchor.lower() in reverse:
            alternatives.append(reverse[anchor.lower()])
        alts = tuple(dict.fromkeys(tuple(_tokens(a)) for a in alternatives if _tokens(a)))
        if alts:
            heads.append(alts)
            head_tokens.update(_tokens(anchor))

    # What the issue is also called, as a whole: the Kenyan names a
    # programme goes by. An acronym's expansion is a HEAD's other name and
    # is matched as one, never as the issue entire.
    acronym_names = {e for a in decompose.acronyms(cleaned) for e in issue_profile.ACRONYMS.get(a.upper(), ())}
    whole = [cleaned, *aliases, *[e for e in issue_profile.expansions(cleaned) if e not in acronym_names]]
    head_alts = {alt for head in heads for alt in head}
    # An expansion that is only a head's name ("Gen Z" for "Gen Z
    # protests") names the head, not the issue.
    phrases = tuple(dict.fromkeys(tuple(_tokens(p)) for p in whole
                                  if _tokens(p) and (p in aliases or tuple(_tokens(p)) not in head_alts)))

    qualifiers: list[tuple[str, tuple[str, ...], str]] = []
    seen: set[str] = set()
    for token in _tokens(cleaned):
        if token in head_tokens or token in GENERIC or len(token) < 3:
            continue
        stem = _stem(token)
        if stem in seen or stem in GENERIC:
            continue
        seen.add(stem)
        qualifiers.append((stem, _variants(stem), token))

    from engine.evidence import connection

    sentence = decompose.is_sentence(cleaned) or connection.is_sentence(cleaned)
    return IssueMatch(issue=issue, phrases=phrases, heads=tuple(heads), qualifiers=tuple(qualifiers),
                      sentence=sentence)


def of(issue: str, aliases=None) -> IssueMatch:
    return _build(issue or "", tuple(a for a in aliases or [] if a and str(a).strip()))
