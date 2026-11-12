"""Is this item about the subject? Decided on the item's own words, never assumed.

The Search report used to link every GDELT, Google News, Reddit, YouTube and
X result to its subject without reading a word of it — "a result returned
for a 'John Mbadi' query is about John Mbadi", on-topic by construction. A
client's report then carried links to Russian newspapers. Measured on live
sources on 11 Nov 2026 (`scripts/relevance_probe.py`):

  * for a subject called "Ministry of Health", one planned GDELT query
    returned 106 articles — German, Chinese, Portuguese, Japanese, Russian
    (amic.ru, abnews.ru, echo.msk.ru) — not one about Kenya, every one linked
    to the subject as coverage of it;
  * for the head of state, 54% of GDELT headlines and 15% of Google News
    headlines never name him: "Nick Kwach Reflects on 16-Year Career" is in
    the corpus because one sentence in it mentions the President.

A search engine returning an item is a reason to LOOK at it. So an item is
the subject's only when its own words say so, by rules a person can check:

  1. It is in a language the engine reads (English or Swahili, and Kenyan
     vernaculars in Latin script). GDELT states the language; a headline in
     another script states it too.
  2. Its headline names the subject — the full name or an alias; or, for an
     item a search for the full name returned, the short name the Kenyan
     press actually prints ("Ruto", "Raila", "KRA").
  3. Or it was read in full and the text names them more than once, or in
     its opening. One mention in passing is not coverage of them.
  4. A subject whose name is generic ("Ministry of Health", "National
     Treasury") must also be marked as Kenya's — by the text or by a Kenyan
     outlet — because every country has one.

Three answers, never two: about them, not about them (with the reason, so it
is counted and shown), and UNVERIFIED — a headline that does not name them
over an article nobody has read yet. Unverified is not "no": the item is
judged again when the article is read. It is not "yes" either.

No model is asked. Primary records (judgments, gazette notices) keep the
strict rule — full name or alias — because a surname in a case title is
often another person.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from engine.reports import outlets, relevance

#: Sources that fetch BY the subject's name. Only for these does a short name
#: in the headline count: the search vouches that the full name is in the
#: item, and the headline says who it is about.
SEARCHED_BY_NAME = frozenset({"google_news_rss", "gdelt", "reddit", "youtube", "wikipedia", "scweet", "twscrape"})

#: Languages the engine reads, as sources state them.
READ_LANGUAGES = frozenset({"english", "swahili", "en", "sw", "eng", "swa", "kiswahili"})

PERSON_TYPES = frozenset({"person", "politician", "individual"})
#: Item kinds with a headline and (perhaps) a body behind it.
ARTICLE_KINDS = frozenset({"article", "news", "video", "document"})
#: The state's own record: strict naming only.
PRIMARY_KINDS = frozenset({"judgment", "gazette", "hansard", "bill", "legislation", "record"})

#: A body shorter than this was not "read in full" — it is a snippet.
READ_IN_FULL_CHARS = 400
#: The opening of a text, in words: named here, the item is about them.
LEDE_WORDS = 60

#: After a person's name these make it a place or a thing named for them —
#: "Raila Odinga Stadium", "Kenyatta National Hospital" — not the person.
_NAMED_FOR = frozenset("""
stadium university hospital avenue road street highway airport market foundation memorial
cup trophy institute college school academy centre center estate ward bridge park library
grounds hall award awards prize lecture scholarship mausoleum museum drive lane way
""".split())
_NAMED_FOR_AFTER_ONE = frozenset({"national", "international", "memorial", "teaching", "referral"})

#: Given names too common to stand for one person. A first name counts as a
#: short name only when it is not one of these: "Raila" names one man,
#: "William" does not.
_COMMON_GIVEN = frozenset("""
william john james peter paul mary martha joseph david charles george samuel daniel stephen steven
anne ann jane grace susan elizabeth margaret rose alice esther ruth sarah rebecca joyce faith mercy
michael robert richard thomas henry edward francis patrick simon moses aaron isaac jacob joshua
johnson jackson wilson benjamin benson nelson alfred albert fred frederick eric erick kevin brian
dennis denis victor vincent anthony antony andrew philip phillip mark luke matthew timothy titus
ali hassan hussein mohamed mohammed muhammad ahmed abdi abdul omar ibrahim yusuf aden adan amina
fatuma halima aisha farah noor said salim suleiman issa musa
christopher christine catherine caroline carol lucy linda nancy agnes beatrice florence gladys
millicent jacqueline josephine judith julia lydia monica pauline priscilla purity rachel sabina
edwin evans felix gideon godfrey hillary isaiah jeremiah julius justin kennedy lawrence leonard
martin maurice nicholas oscar oliver raphael reuben rodgers ronald stanley sylvester wycliffe
""".split())

#: Words that name a category of institution rather than one institution.
#: A subject made only of these needs marking as Kenya's.
_GENERIC = relevance.GENERIC_TERMS | frozenset("""
national state department office public service services central bank university county
republic agency bureau institute fund corporation company federal supreme high electoral
revenue immigration defence defense interior foreign affairs labour labor justice sports
culture information communication communications technology planning devolution infrastructure
roads works petroleum cooperatives livestock fisheries gender social protection security
internal attorney general chief registrar auditor controller budget ombudsman president
presidency deputy minister ministers secretary principal economy economic development
""".split())
_FILLER = frozenset({"of", "the", "and", "for", "on", "in", "a", "an", "to"})

#: What may stand directly before a surname in a headline without being
#: somebody else's first name: an office, a courtesy, or the word a
#: sentence opens with.
_BEFORE_A_NAME = frozenset("""
president pres dp deputy mr mrs ms dr prof professor hon honourable honorable sen senator gov
governor mp mca cs ps minister premier pm former ex excellency he chief comrade mheshimiwa rais
bwana baba mama king queen justice judge speaker leader boss team kenya kenyan
why how as after when what will did does is has can should where who if with for from to in on
at by and but or of the a an before since while over under against about says said tells told
breaking live watch video photos opinion analysis editorial exclusive update just now today new
news inside meet not no
""".split())

_POSSESSIVE = re.compile(r"(?<=\w)['’`]s\b")
_APOSTROPHE = re.compile(r"['’`]")
_WORD = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Text as it is matched: fancy Unicode letters made plain (a Facebook
    post set in mathematical bold is still English), possessives and
    apostrophes dropped ("Ruto's" is Ruto; "Matiang'i" is "Matiangi"), case
    and punctuation gone."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _POSSESSIVE.sub("", text)
    text = _APOSTROPHE.sub("", text)
    return " ".join(_WORD.findall(text.lower()))


_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _tokens(text: str) -> list[tuple[str, str, int, int]]:
    """(word as matched, word as printed, start, end) — the printed form and
    the positions are what tell "Charlene Ruto" from "Why Ruto"."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _APOSTROPHE.sub("", _POSSESSIVE.sub("", text))
    return [(m.group().lower(), m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def _adjacent(a: tuple, b: tuple) -> bool:
    """Nothing but one space apart — a comma or a dash between two names
    makes them two people."""
    return b[2] - a[3] == 1


def _is_sentence_case(tokens: list) -> bool:
    """A headline whose capitals carry meaning: most words after the first
    start small. "Kenyans Tell Ruto To Resign" is not one."""
    later = [t[1] for t in tokens[1:] if t[1][:1].isalpha() and len(t[1]) >= 3]
    return bool(later) and sum(1 for w in later if w[:1].islower()) / len(later) >= 0.4


def unread_language(headline: str, stated: str | None) -> str | None:
    """The language of an item the engine does not read, or None."""
    said = (stated or "").strip().lower()
    if said and said not in READ_LANGUAGES:
        return said.title()
    letters = [c for c in unicodedata.normalize("NFKC", headline or "") if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c) > 0x24F) / len(letters) > 0.3:
        return "a script other than Latin"
    return None


def is_generic_name(name: str) -> bool:
    """True when the name alone could be any country's: "Ministry of Health",
    "National Treasury", "Senate". "Central Bank of Kenya" is not — it says."""
    words = [w for w in _WORD.findall((name or "").lower()) if w not in _FILLER]
    return bool(words) and all(w in _GENERIC for w in words)


def _acronym(name: str) -> str | None:
    """KRA, IEBC — the initials of a long institutional name, as printed."""
    caps = [w for w in re.findall(r"[A-Za-z][A-Za-z'’]*", name or "")
            if w[0].isupper() and w.lower() not in _FILLER]
    return "".join(w[0] for w in caps) if len(caps) >= 3 else None


@dataclass(frozen=True)
class Verdict:
    matched: bool
    match_type: str
    confidence: float = 0.0
    #: Why it is not the subject's, in words a reader can check. None when it is.
    reason: str | None = None
    #: False for the third answer: judge again when more of the item is read.
    final: bool = True


class Gate:
    """One subject's rule, compiled once and asked of every item."""

    def __init__(self, name: str, aliases=None, subject_type: str | None = None):
        self.name = (name or "").strip()
        self.is_person = (subject_type or "politician") in PERSON_TYPES
        strong = [self.name, *[a for a in (aliases or []) if a]]
        ministry = re.match(r"^\s*ministry\s+of\s+(.+)$", self.name, re.I)
        if ministry:
            strong.append(f"{ministry.group(1)} Ministry")
        self.strong = self._distinct(strong)
        weak: list[str] = []
        parts = [p for p in re.split(r"\s+", self.name) if p]
        if self.is_person and len(parts) >= 2:
            last, first = parts[-1], parts[0]
            if len(fold(last)) >= 4:
                weak.append(last)
            if len(fold(first)) >= 4 and fold(first) not in _COMMON_GIVEN:
                weak.append(first)
        self.weak = self._distinct(weak, exclude=self.strong)
        self._surname = fold(parts[-1]) if self.is_person and len(parts) >= 2 else ""
        acronym = None if self.is_person else _acronym(self.name)
        self.acronym = re.compile(rf"(?<![A-Za-z0-9]){re.escape(acronym)}(?![A-Za-z0-9])") if acronym else None
        self.needs_anchor = (not self.is_person) and is_generic_name(self.name)
        self._own_words = set(fold(self.name).split())

    @staticmethod
    def _distinct(forms, exclude=()) -> list[tuple[str, ...]]:
        seen = {tuple(e) for e in exclude}
        out = []
        for form in forms:
            words = tuple(fold(str(form)).split())
            if words and words not in seen:
                seen.add(words)
                out.append(words)
        return sorted(out, key=len, reverse=True)

    # -- counting --------------------------------------------------------

    def _named_for_something(self, words: list[str], end: int) -> bool:
        """The name is followed by what makes it a place or a thing."""
        if not self.is_person or end >= len(words):
            return False
        nxt = words[end]
        if nxt in _NAMED_FOR and nxt not in self._own_words:
            return True
        return (nxt in _NAMED_FOR_AFTER_ONE and end + 1 < len(words)
                and words[end + 1] in _NAMED_FOR)

    def _another_person(self, tokens: list, at: int, form: tuple, sentence_case: bool) -> bool:
        """A short name that is half of somebody else's: "Charlene Ruto",
        "Ida Odinga", "Raila Junior". Told only where capitals mean something
        — a headline in sentence case — and only for a word set directly
        beside the short name, with nothing but a space between."""
        if not sentence_case or len(form) != 1:
            return False
        word = form[0]
        is_surname = bool(self._surname) and word == self._surname
        if is_surname and at > 0:
            before = tokens[at - 1]
            if (_adjacent(before, tokens[at]) and before[1][:1].isupper()
                    and before[0] not in self._own_words and before[0] not in _BEFORE_A_NAME):
                return True
        if not is_surname and at + 1 < len(tokens):
            after = tokens[at + 1]
            if (_adjacent(tokens[at], after) and after[1][:1].isupper()
                    and after[0] not in self._own_words):
                return True
        return False

    def count(self, text: str, weak: bool) -> tuple[int, int]:
        """(times named by full name or alias, times by short name only)."""
        tokens = _tokens(text)
        words = [t[0] for t in tokens]
        sentence_case = _is_sentence_case(tokens)
        forms = [(f, True) for f in self.strong] + ([(f, False) for f in self.weak] if weak else [])
        strong_n = weak_n = i = 0
        while i < len(words):
            for form, is_strong in forms:
                if tuple(words[i:i + len(form)]) == form:
                    if not (self._named_for_something(words, i + len(form))
                            or (not is_strong and self._another_person(tokens, i, form, sentence_case))):
                        strong_n += is_strong
                        weak_n += not is_strong
                    i += len(form) - 1
                    break
            i += 1
        if weak and self.acronym is not None:
            weak_n += len(self.acronym.findall(unicodedata.normalize("NFKC", text or "")))
        return strong_n, weak_n

    # -- the judgement ---------------------------------------------------

    def judge(self, item) -> Verdict:
        get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
        raw = get("raw_payload") or {}
        if not isinstance(raw, dict):
            raw = {}
        text = str(get("text") or "")
        kind = str(get("source_type") or "")
        source = raw.get("source") if isinstance(raw.get("source"), str) else ""
        searched = source in SEARCHED_BY_NAME and kind not in PRIMARY_KINDS
        article = kind in ARTICLE_KINDS
        body = raw.get("article_text") if isinstance(raw.get("article_text"), str) else ""
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        if article:
            headline = (title or text.split("\n", 1)[0])[:400]
            if not body and "\n" in text:
                body = text.split("\n", 1)[1]
        else:
            headline, body = text, ""        # a post is all headline
        read_in_full = len(body.strip()) >= READ_IN_FULL_CHARS

        language = unread_language(headline, raw.get("language"))
        if language:
            return Verdict(False, "unread_language",
                           reason=f"written in {language}, which the engine does not read")

        strong, weak = self.count(headline, searched)
        verdict = None
        if strong:
            verdict = Verdict(True, "named_in_headline" if article else "named_in_text", 1.0)
        elif weak:
            verdict = Verdict(True, "short_name_in_headline" if article else "short_name_in_text", 0.9)
        elif read_in_full:
            s, w = self.count(body, searched)
            opening = " ".join(body.split()[:LEDE_WORDS])
            s0, w0 = self.count(opening, searched)
            if s + w >= 2 or s0 + w0:
                verdict = Verdict(True, "named_in_text", 0.85)
            elif s + w == 1:
                return Verdict(False, "passing_mention", reason="names them once, in passing")
            else:
                return Verdict(False, "not_named", reason="read in full and never names them")
        elif article and searched:
            return Verdict(False, "unverified", final=False,
                           reason="the headline does not name them and the article has not been read")
        else:
            return Verdict(False, "not_named", reason="does not name them")

        if self.needs_anchor and not self._marked_as_kenyan(item, get, raw, f"{headline}\n{body}"):
            return Verdict(False, "no_market_anchor", final=read_in_full or not article,
                           reason=f"names a {self.name} without anything marking it as Kenya's")
        return verdict

    def _marked_as_kenyan(self, item, get, raw: dict, text: str) -> bool:
        if relevance.mentions_any(text, relevance.MARKET_TERMS):
            return True
        if str(raw.get("source_country") or raw.get("sourcecountry") or "").strip().lower() == "kenya":
            return True
        as_mention = {
            "platform": get("platform"), "source_type": get("source_type"),
            "author_handle": get("author_handle"), "source_url": get("source_url"),
            "outlet": raw.get("outlet"), "outlet_domain": raw.get("outlet_domain"),
            "source_country": raw.get("source_country"),
        }
        try:
            return outlets.classify(as_mention)[0] == outlets.LOCAL
        except Exception:  # noqa: BLE001 — an outlet nobody can place is not Kenyan by default
            return False


def gate_for(subject) -> Gate:
    return Gate(subject.name, getattr(subject, "aliases", None) or [],
                getattr(subject, "subject_type", None))


def parent_keys(item) -> set[str]:
    """What a reaction names as the post it sits under."""
    raw = (item.get("raw_payload") if isinstance(item, dict) else getattr(item, "raw_payload", None)) or {}
    if not isinstance(raw, dict):
        return set()
    keys = set()
    parent = raw.get("_parent_post")
    if isinstance(parent, dict):
        keys.update(str(parent.get(k)) for k in ("id", "url") if parent.get(k))
    elif parent:
        keys.add(str(parent))
    for field in ("video_id", "url"):
        if raw.get(field) and parent:
            keys.add(str(raw[field]))
    return keys


def own_keys(item) -> set[str]:
    """What a post can be named by, by a reaction under it."""
    raw = (item.get("raw_payload") if isinstance(item, dict) else getattr(item, "raw_payload", None)) or {}
    if not isinstance(raw, dict):
        raw = {}
    url = item.get("source_url") if isinstance(item, dict) else getattr(item, "source_url", None)
    keys = {str(v) for v in (url, raw.get("url"), raw.get("video_id"), raw.get("id"), raw.get("post_id")) if v}
    post = raw.get("post")
    if isinstance(post, dict):
        keys.update(str(post.get(k)) for k in ("id", "url", "video_id") if post.get(k))
    return keys


class Tally:
    """What the gate decided over one report's collection — counted, with an
    example of each reason, because an item set aside must be said."""

    def __init__(self):
        self.examined = 0
        self.about = 0
        self.unverified = 0
        self.reasons: Counter = Counter()
        self.examples: dict[str, list[str]] = {}
        self.relinked = 0
        self.unlinked = 0

    def record(self, verdict: Verdict, item) -> None:
        self.examined += 1
        if verdict.matched:
            self.about += 1
            return
        if not verdict.final:
            self.unverified += 1
        reason = verdict.reason or "does not name them"
        self.reasons[reason] += 1
        text = str((item.get("text") if isinstance(item, dict) else getattr(item, "text", "")) or "")
        shown = self.examples.setdefault(reason, [])
        if len(shown) < 3:
            shown.append(" ".join(text.split())[:140])

    def summary(self) -> dict:
        return {"examined": self.examined, "about_the_subject": self.about,
                "set_aside": sum(self.reasons.values()) - self.unverified,
                "unverified": self.unverified,
                "reasons": dict(self.reasons.most_common()),
                "examples": self.examples,
                "earlier_links_withdrawn": self.unlinked,
                "basis": ("an item is counted as about the subject only when its own words name "
                          "them; a search returning it is not enough")}
