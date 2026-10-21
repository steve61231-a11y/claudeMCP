"""Who published an item, and whether that is local, international or social.

The framework segments coverage into local media, international media and
social media, and an investor found it "makes mistakes when it comes to
identifying local and international coverage". It did, systematically:

  * Google News items carried the outlet NAME squashed into a key —
    "thestar", "nationafrica" — and the classifier looked for DOMAINS such
    as "the-star.co.ke". Nothing matched, so the Daily Nation, The Star and
    Citizen Digital were all counted as international coverage.
  * Kenya's biggest newsrooms do not all live on .ke: nation.africa,
    citizen.digital, peopledaily.digital, capitalfm.africa, k24.digital,
    tv47.digital, businessdailyafrica.com, thekenyatimes.com.
  * A Kenyan broadcaster's YouTube channel (NTV Kenya, Citizen TV) is that
    newsroom's coverage, not a social post — while a vlogger on the same
    platform is social media.
  * Google News lists Facebook posts as an outlet called "Facebook" — 427 in
    one probe — which are social media, not foreign press.

The registries below were built from what the sources actually returned on
21 Oct 2026 (scripts/data_probe.py: Google News outlets and their own
domains, GDELT domains with their source country, YouTube channels), not
from memory. Order of evidence: a reaction is social; a known publisher
decides; then the outlet's own domain; then GDELT's source country; then the
outlet's name; and only then the default — international — which is said to
be a default in the basis, so it can be challenged.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

LOCAL = "local_media"
INTERNATIONAL = "international_media"
SOCIAL = "social_media"

#: Platforms where anyone can publish. An item here is social media unless a
#: known newsroom published it.
SOCIAL_PLATFORMS = {
    "twitter", "x", "facebook", "instagram", "tiktok", "youtube", "linkedin",
    "reddit", "threads", "telegram", "whatsapp", "snapchat",
}
SOCIAL_DOMAINS = {
    "facebook.com", "fb.com", "m.facebook.com", "youtube.com", "m.youtube.com",
    "youtu.be", "x.com", "twitter.com", "t.co", "tiktok.com", "instagram.com",
    "linkedin.com", "reddit.com", "old.reddit.com", "threads.net", "t.me",
    "telegram.me", "whatsapp.com",
}

#: Kenyan publishers whose sites are NOT on .ke — the ones a suffix check
#: misses. Everything under .ke is Kenyan by construction.
KENYAN_DOMAINS = {
    "nation.africa", "citizen.digital", "peopledaily.digital", "capitalfm.africa",
    "k24.digital", "tv47.digital", "businessdailyafrica.com", "thekenyatimes.com",
    "kenyanwallstreet.com", "kenyainsights.com", "nairobiwire.com", "hapakenya.com",
    "theelephant.info", "kenyan-post.com", "mwakilishi.com", "kahawatungu.com",
    "kenya-today.com", "radio47.fm", "thekenyandiaspora.com", "biznakenya.com",
    "khusoko.com", "nairobilawmonthly.com", "techweez.com", "kenyanvibe.com",
    "amnestykenya.org", "icj-kenya.org", "katibainstitute.org", "sokodirectory.com",
    "uchumi360.com", "malindikenya.net", "techtrendske.co.ke", "ghafla.com",
    "footballkenya.org", "katakenya.org", "kenyanreport.com", "the-star.co.ke",
    "standardmedia.co.ke", "kenyans.co.ke", "tuko.co.ke", "kbc.co.ke",
    "taifaleo.nation.co.ke", "nation.co.ke", "theeastafrican.co.ke",
    "mountkenyatimes.co.ke", "eastleighvoice.co.ke", "ntvkenya.co.ke",
    "capitalfm.co.ke", "pulselive.co.ke", "kenyanews.go.ke", "judiciary.go.ke",
    "parliament.go.ke", "mzalendo.com", "africog.org", "tifaresearch.com",
    "infotrakresearch.com", "kenyanwallstreet.co.ke", "nairobileo.co.ke",
}

#: Kenyan newsrooms and institutions by NAME — for rows stored before the
#: outlet's domain was kept, and for YouTube channels, where the publisher
#: is a channel name. Matched on letters and digits only, so "The Star,
#: Kenya", "thestar,kenya" and "THE STAR KENYA" are one key.
KENYAN_NAMES = {
    "dailynation", "nation", "nationafrica", "nationmediagroup", "ntvkenya", "ntv",
    "thestar", "thestarkenya", "thestarke", "citizendigital", "citizentv",
    "citizentvkenya", "radiocitizen", "royalmedia", "thestandard", "standarddigital",
    "standardmedia", "thestandardmediagroup", "ktnnews", "ktnnewskenya", "ktnhome",
    "kenyansco", "kenyanscoke", "tuko", "tukonews", "tukocoke", "peopledaily",
    "capitalfm", "capitalfmafrica", "capitalfmkenya", "kbc", "kbcdigital",
    "kbcchannel1", "kbcsports", "kenyabroadcastingcorporation", "k24", "k24tv",
    "k24digital", "tv47", "tv47kenya", "tv47digital", "businessdaily",
    "businessdailyafrica", "theeastafrican", "kenyanewsagency", "thekenyatimes",
    "thekenyanwallstreet", "theeastleighvoice", "nairobileo", "nairobiwire",
    "kahawatungu", "kenyainsights", "hapakenya", "theelephant", "mwakilishi",
    "pulsekenya", "pulselivekenya", "ghafla", "mpasho", "kdrtv", "radio47",
    "radio47hapandipo", "spicefm", "radiogenerationkenya", "ramogitvkenya",
    "ramogitv", "inoorotv", "kametv", "kamemetv", "kasstv", "kassmedia",
    "uzalendonews", "emsdigitalke", "parliamentofkenya", "nationalassemblykenya",
    "senatekenya", "kenyajudiciary", "thejudiciarykenya", "statehousekenya",
    "eacc", "ethicsanticorruptioncommission", "mzalendo", "taifaleo",
    "thekenyandiasporamedia", "money254", "businesstodaykenya", "vellumkenya",
    "saccoreview", "radiokaya", "sifafmkenya", "themtkenyatimes", "kenyanpost",
    "kenyatoday", "bizna", "biznakenya", "khusoko", "techweez",
    "nairobicitycounty", "eastleighvoice", "kenyanreport", "hivisasa",
}

#: International broadcasters and agencies — needed for YouTube, where a
#: channel otherwise reads as social media.
INTERNATIONAL_NAMES = {
    "aljazeera", "aljazeeraenglish", "bbc", "bbcnews", "bbcnewsafrica", "bbcworldservice",
    "dw", "dwnews", "dwafrica", "france24", "france24english", "reuters", "cnn",
    "cnninternational", "cnnnews18", "sabcnews", "trtafrika", "trtworld", "africanews",
    "ndtv", "ndtvworld", "firstpost", "forbesbreakingnews", "ubctelevisionuganda",
    "cgtnafrica", "cgtn", "voaafrica", "voanews", "skynews", "channelstelevision",
    "arisenews", "apnews", "associatedpress", "afp", "afpnewsagency", "bloomberg",
    "bloombergtelevision", "financialtimes", "theguardian", "theeconomist",
    "wion", "euronews", "nbcnews", "abcnews", "cbsnews", "foxnews", "xinhua",
    "rfi", "theafricareport", "allafrica",
}

#: Platforms that are one feed to a reader — "X", not each account on it.
#: YouTube is not here: a channel is a publisher (NTV Kenya's, or a
#: vlogger's), and the sources list names it.
_FEEDS = {"x", "twitter", "reddit", "tiktok", "instagram", "facebook", "linkedin", "threads"}

#: The name a reader knows a newsroom by, from its own domain.
DOMAIN_NAMES = {
    "nation.africa": "Daily Nation", "nation.co.ke": "Daily Nation",
    "the-star.co.ke": "The Star", "standardmedia.co.ke": "The Standard",
    "citizen.digital": "Citizen Digital", "capitalfm.co.ke": "Capital FM",
    "capitalfm.africa": "Capital FM", "kenyans.co.ke": "Kenyans.co.ke",
    "tuko.co.ke": "Tuko", "businessdailyafrica.com": "Business Daily",
    "theeastafrican.co.ke": "The EastAfrican", "kbc.co.ke": "KBC",
    "peopledaily.digital": "People Daily", "k24.digital": "K24", "tv47.digital": "TV47",
    "kenyanews.go.ke": "Kenya News Agency", "pulselive.co.ke": "Pulse Kenya",
    "nairobileo.co.ke": "Nairobi Leo", "eastleighvoice.co.ke": "The Eastleigh Voice",
    "thekenyatimes.com": "The Kenya Times", "ntvkenya.co.ke": "NTV Kenya",
    "kenyalaw.org": "Kenya Law", "parliament.go.ke": "Parliament of Kenya",
    "reuters.com": "Reuters", "bbc.com": "BBC", "bbc.co.uk": "BBC",
    "aljazeera.com": "Al Jazeera", "apnews.com": "AP News", "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times", "theguardian.com": "The Guardian",
    "economist.com": "The Economist", "africanews.com": "Africanews",
    "allafrica.com": "AllAfrica", "theafricareport.com": "The Africa Report",
    "cnn.com": "CNN", "edition.cnn.com": "CNN", "dw.com": "DW", "france24.com": "France 24",
    "voanews.com": "VOA", "semafor.com": "Semafor", "nytimes.com": "The New York Times",
    "washingtonpost.com": "The Washington Post", "thecitizen.co.tz": "The Citizen (Tanzania)",
    "monitor.co.ug": "Daily Monitor (Uganda)",
}

#: Spellings of one newsroom's name, keyed on letters and digits only.
CANONICAL_NAMES = {
    "dailynation": "Daily Nation", "nationafrica": "Daily Nation", "nation": "Daily Nation",
    "thestar": "The Star", "thestarkenya": "The Star", "thestarke": "The Star",
    "thestandard": "The Standard", "standarddigital": "The Standard",
    "standardmedia": "The Standard", "citizendigital": "Citizen Digital",
    "capitalfm": "Capital FM", "capitalfmafrica": "Capital FM", "capitalfmkenya": "Capital FM",
    "kenyanscoke": "Kenyans.co.ke", "tuko": "Tuko", "tukonews": "Tuko", "tukocoke": "Tuko",
    "businessdaily": "Business Daily", "businessdailyafrica": "Business Daily",
    "theeastafrican": "The EastAfrican", "kbc": "KBC", "kbcdigital": "KBC",
    "peopledaily": "People Daily", "k24digital": "K24", "k24": "K24",
    "tv47digital": "TV47", "tv47": "TV47", "kenyanewsagency": "Kenya News Agency",
}

#: Words that mark a publisher as Kenyan when nothing better is known.
_KENYAN_WORDS = re.compile(r"\b(kenya|kenyan|kenyans|nairobi|mombasa|kisumu|nakuru|eldoret)\b|254|🇰🇪", re.I)

#: Hosts that carry other publishers' items and say nothing about them.
_AGGREGATOR_HOSTS = {"news.google.com", "google.com", "msn.com", "yahoo.com", "flipboard.com"}


def _key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _host(url: str | None) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_kenyan_domain(host: str) -> bool:
    if not host:
        return False
    if host.endswith(".ke") or host in KENYAN_DOMAINS:
        return True
    return any(host.endswith("." + d) for d in KENYAN_DOMAINS)


def _is_social_domain(host: str) -> bool:
    return host in SOCIAL_DOMAINS or any(host.endswith("." + d) for d in SOCIAL_DOMAINS)


def publisher(mention: dict) -> str:
    """The name a reader knows this item's publisher by.

    One newsroom, one name. GDELT reports a domain and Google News an outlet
    name, so the Daily Nation arrived in one sources list as two sources —
    "nation.africa" and "Daily Nation" — each with half its coverage. And for
    an article the author is a journalist, not the publisher: grouping on it
    split one outlet into as many rows as it has bylines."""
    platform = (mention.get("platform") or "").strip()
    kind = mention.get("source_type")
    if kind == "comment":
        return f"{_pretty_platform(platform)} comments"
    if platform.lower() in _FEEDS:
        return _pretty_platform(platform)
    outlet = (mention.get("outlet") or "").strip()
    if outlet and outlet.lower() not in ("google_news", "news"):
        return _canonical(outlet)
    domain = (mention.get("outlet_domain") or "").lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    if domain in DOMAIN_NAMES:
        return DOMAIN_NAMES[domain]
    handle = (mention.get("author_handle") or "").strip()
    if platform.lower() in SOCIAL_PLATFORMS:
        # A channel on a platform anyone can post to: the channel IS the
        # publisher — NTV Kenya's channel, or a vlogger's.
        return _canonical(handle) if handle else _pretty_platform(platform)
    if "." in platform:
        return DOMAIN_NAMES.get(platform.lower()) or platform
    if platform and platform.lower() not in ("news", "google_news", "web", "unknown"):
        return _canonical(handle) if handle else _pretty_platform(platform)
    return _canonical(handle) if handle else (_pretty_platform(platform) or "unknown")


def _canonical(name: str) -> str:
    """One spelling per newsroom: "The Star, Kenya" and "THE STAR KENYA"
    are the same publisher as the-star.co.ke."""
    return CANONICAL_NAMES.get(_key(name), name)


def _pretty_platform(platform: str) -> str:
    names = {"x": "X", "twitter": "X", "youtube": "YouTube", "reddit": "Reddit",
             "tiktok": "TikTok", "instagram": "Instagram", "facebook": "Facebook",
             "linkedin": "LinkedIn", "kenyalaw": "Kenya Law", "wikipedia": "Wikipedia"}
    return names.get((platform or "").lower(), platform or "")


def classify(mention: dict) -> tuple[str, str]:
    """`(segment, basis)` for one corpus item. The basis says which evidence
    decided, so a disputed call can be traced rather than argued about."""
    platform = (mention.get("platform") or "").strip().lower()
    kind = mention.get("source_type")
    domain = (mention.get("outlet_domain") or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain:
        host = _host(mention.get("source_url"))
        if host and host not in _AGGREGATOR_HOSTS:
            domain = host
    name_key = _key(mention.get("outlet") or mention.get("author_handle"))

    if kind == "comment":
        return SOCIAL, "a reaction, not a publication"
    if platform == "kenyalaw" or kind in ("judgment", "gazette"):
        return LOCAL, "Kenyan primary record"
    if platform in SOCIAL_PLATFORMS or _is_social_domain(domain):
        # The publisher decides on a platform anyone can post to: NTV Kenya's
        # channel is NTV's coverage; a vlogger's is social media.
        if name_key in KENYAN_NAMES:
            return LOCAL, "Kenyan newsroom publishing on a social platform"
        if name_key in INTERNATIONAL_NAMES:
            return INTERNATIONAL, "international newsroom publishing on a social platform"
        return SOCIAL, "social platform"
    if domain:
        if _is_kenyan_domain(domain):
            return LOCAL, f"Kenyan outlet ({domain})"
        country = (mention.get("source_country") or "").strip().lower()
        if country == "kenya":
            return LOCAL, "GDELT places the outlet in Kenya"
        if name_key in KENYAN_NAMES:
            return LOCAL, "known Kenyan outlet"
        if country:
            return INTERNATIONAL, f"GDELT places the outlet in {mention.get('source_country')}"
        return INTERNATIONAL, f"non-Kenyan outlet ({domain})"
    if name_key in KENYAN_NAMES:
        return LOCAL, "known Kenyan outlet"
    if name_key in INTERNATIONAL_NAMES:
        return INTERNATIONAL, "known international outlet"
    # A squashed key from before outlet domains were kept: "kenyans.co.ke"
    # survives as a platform value with its dots intact.
    if _is_kenyan_domain(platform):
        return LOCAL, f"Kenyan outlet ({platform})"
    if _KENYAN_WORDS.search(mention.get("outlet") or mention.get("author_handle") or ""):
        return LOCAL, "Kenyan by name"
    return INTERNATIONAL, "no evidence it is Kenyan (default)"
