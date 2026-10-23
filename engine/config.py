import os

from pydantic_settings import BaseSettings

# huggingface.co isn't on most deployment egress allowlists for this engine,
# and huggingface_hub's connection retry/backoff (~30s) runs per file fetch
# inside model loading, independent of our own fallback caching. Default to
# offline mode so missing HF access fails in milliseconds, not minutes;
# `os.environ.setdefault` still lets an operator override this explicitly
# (e.g. HF_HUB_OFFLINE=0) where HF Hub access is actually configured.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class Settings(BaseSettings):
    # "local-dev" enables convenience fallbacks (the default local database).
    # Anything else requires explicit credentials, and /api/health reports
    # what is missing (`configuration_problems`).
    app_env: str = "local-dev"
    database_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    # Cheap/fast model for the high-volume mechanical stages (classification,
    # disambiguation, per-item sentiment, map-step digestion). Running those on
    # the strong model is what makes whole-corpus coverage unaffordable; running
    # them here is what makes "analyse everything" practical. Reasoning and
    # verification stay on anthropic_model. Empty = use anthropic_model.
    anthropic_bulk_model: str = "claude-haiku-4-5-20251001"
    # Which backend serves LLM calls. "anthropic" is the only one fit for a
    # client report; the others exist so testing does not have to be paid for
    # at production rates:
    #   anthropic         — the real thing (default)
    #   openai_compatible — any provider speaking OpenAI's /chat/completions
    #                       (DeepSeek, Qwen/DashScope, GLM/Zhipu, Kimi, a local
    #                       Ollama). Set llm_base_url + llm_api_key + the model
    #                       names. Cheap or free, and good enough to prove the
    #                       pipeline runs — see the warning on `is_test_grade`.
    #   stub              — no network, no cost, deterministic canned JSON. For
    #                       exercising connectors, the database, orchestration
    #                       and the frontend, where the model's answer is not
    #                       what is under test.
    llm_provider: str = "anthropic"
    llm_base_url: str = ""       # e.g. https://api.deepseek.com/v1
    llm_api_key: str = ""
    llm_model: str = ""          # strong-tier model name on that provider
    llm_bulk_model: str = ""     # bulk-tier model name; empty = llm_model
    # Ceiling on parallel LLM calls. Free tiers cap requests-per-minute far
    # below what the map step wants, and a 429 storm looks like a broken run.
    # 0 = no ceiling (the default, for Anthropic).
    llm_max_concurrency: int = 0
    # Extra JSON merged into each openai_compatible request body, for
    # provider-specific fields (e.g. '{"thinking": {"type": "disabled"}}' on
    # GLM). An escape hatch so a provider quirk never needs a code change.
    llm_extra_body: str = ""
    # Characters of corpus per map-step call. Free tiers meter REQUESTS per day,
    # not tokens, so on a large-context model fewer, bigger chunks is the
    # difference between finishing a report and hitting the daily cap. 0 = the
    # module default (16k, sized for a 200k-context model).
    llm_chunk_chars: int = 0
    # Minimum gap between outbound LLM requests, milliseconds. Capping
    # concurrency is not enough on a per-minute quota: N workers still fire N
    # requests at once and the provider sees a burst. Spacing them keeps the
    # same total under the limit rather than tripping it and then waiting out a
    # penalty window. 0 = no spacing (the default, for Anthropic).
    llm_min_request_interval_ms: int = 0
    # Ceiling on a single response, in output tokens. The OpenAI-compatible
    # path is bound by DeepSeek's 8192 and clamps itself; Claude will write far
    # more than that, and depth is the entire point of the analyst sections, so
    # the paid backend is not held to a stand-in's limit. 0 = the per-provider
    # default (8k on openai_compatible, 16k on Anthropic).
    llm_max_output_tokens: int = 0
    # Items per batched LLM call for those bulk stages.
    agent_batch_size: int = 25
    # Post-generation audit: decompose the finished report into atomic claims and
    # check each against stored evidence, recording status + citations. This is
    # the guard against a fluent, confident, wrong sentence reaching a client.
    enable_verification: bool = True
    # Entity/event resolution: how many corpus items get the extraction pass per
    # run. Bounded because it is the richest (and priciest) per-item call; the
    # rest carry over to the next incremental run.
    enable_resolution: bool = True
    resolution_max_items: int = 300
    # The verified fact store (evidence/facts.py): each source read once, every
    # fact anchored to a quote found verbatim in it. Runs after delivery, on
    # sources not yet read. The cap bounds spend per run; the corpus compounds,
    # so what one run leaves the next one reads.
    enable_fact_store: bool = True
    fact_extraction_max_items: int = 200
    # How much of one source is read — a whole article, not its first
    # paragraph. Each extra 1,000 characters is ~250 input tokens per source.
    fact_item_chars: int = 6000
    # How long a report waits, after its analysts return, for the facts being
    # read alongside them. Past this the report goes out with what is stored
    # and the back office reads the rest.
    fact_store_wait_seconds: float = 60.0
    # Investigator: after the report, name what is missing and turn those gaps
    # into queries the next run chases. This is what makes successive runs an
    # investigation rather than a repeated sweep.
    enable_investigator: bool = True
    sentiment_confidence_threshold: float = 0.55
    newsapi_key: str = ""
    socialcrawl_api_key: str = ""
    # SocialCrawl is the SOCIAL-MEDIA backbone: whenever credits are available it
    # runs as the primary source for TikTok/Instagram/LinkedIn/X/YouTube. The
    # moment the balance drops below this floor, the run switches immediately to
    # the free social scrapers instead of burning the run on 402s. (News,
    # archives and web discovery are separate sources and always run alongside.)
    socialcrawl_min_credits: float = 1.0
    # Premium (10cr) video transcripts fetched per run, for the most-engaged
    # videos already linked to the politician. 0 disables transcripts.
    transcripts_per_run: int = 15
    # Minimum follower count for an author to be classified as an influencer.
    influencer_follower_threshold: int = 1000
    # Demo API (engine/api_server.py) hardening. When pulse_api_key is set,
    # /api/report requires a matching X-API-Key header. allowed_origins is a
    # comma-separated CORS allowlist; empty keeps the permissive dev default.
    pulse_api_key: str = ""
    allowed_origins: str = ""
    report_rate_limit_per_hour: int = 20
    # Local ML models (transformers sentiment + sentence-transformers embeddings)
    # pull PyTorch, which OOM-kills a 512MB free instance. OFF by default so the
    # pipeline uses its LLM-sentiment + TF-IDF-narrative fallbacks (no torch) and
    # fits free hosting. Set USE_LOCAL_ML=true on a box with >=2GB RAM for the
    # faster/cheaper local models.
    use_local_ml: bool = False
    # Low-memory mode for tiny (512MB) free instances: caps concurrency, skips
    # the heaviest steps (YouTube comment extraction) and bounds how many
    # mentions get per-item LLM work, so a report run can't OOM-kill the worker
    # (the cause of intermittent 502s on free hosting). Turn OFF on a >=2GB box
    # for fuller/faster runs.
    low_memory: bool = True
    low_memory_max_llm_mentions: int = 140  # cap per-mention LLM work per report
    # How wide a parallel stage of MODEL CALLS may run.
    #
    # This used to be bundled into `low_memory`, which is on by default — so
    # every model fan-out in the product ran at a third to a quarter of its
    # width on a default deployment. Measured on a 400-mention corpus with
    # the model stubbed to a fixed latency so only the structure shows: the
    # corpus digest alone, at two workers instead of six, was 56% of the
    # run's wall clock with nothing else in flight. That is most of "it's a
    # bit slow".
    #
    # The bundling was a category error. `low_memory` protects RAM on a
    # 512MB instance; these are threads blocked on an HTTP response. A
    # digest call holds a 16KB prompt and returns about as much, so six in
    # flight cost under 200KB of the 512MB they were protecting. They bought
    # no memory and they cost minutes.
    #
    # Memory protection stays where it belongs: NER, YouTube comment
    # extraction, and the cap on per-mention work. To throttle a
    # rate-limited or metered backend the right knob is LLM_MAX_CONCURRENCY,
    # which is now a real process-wide in-flight limit rather than a
    # per-stage one that overlapping stages could multiply.
    # Raised from 8. Fifteen analyst sections at eight workers is two waves,
    # and the second wave is pure wall-clock the reader waits through for no
    # reason — these threads sit on an HTTP response. One wave instead of
    # two is the single biggest latency win in the run, and it costs about
    # 200KB. See `LLM_MAX_CONCURRENCY` for the limit that actually bounds
    # spend; this one only decides how long the waiting takes.
    llm_fanout_workers: int = 16
    # The same separation for the collectors.
    #
    # A scraper worker holds a response body, which is a real allocation —
    # so unlike the model fan-out above, the memory argument here is not
    # imaginary. It is still the wrong lever: `ingestion/http.py` already
    # paces per host, pages are parsed and committed as they land rather
    # than accumulated, and three workers against a dozen sources is most of
    # why collection is the longest phase of a run. Lower this, not
    # LOW_MEMORY, if a small instance is actually running out of room.
    # Collectors are I/O-bound and mostly on DIFFERENT hosts, so widening
    # them overlaps the waiting rather than multiplying it. The per-host gap
    # in `ingestion/http.py` still paces GDELT at one request per five
    # seconds whatever this says — a polite source stays polite.
    ingestion_workers: int = 14
    # Wall-clock ceiling on collection. Tasks not started by then are recorded
    # as not asked (never as empty — the learning loop reads only finished
    # tasks); tasks in flight stop at their next page or download and store
    # what they have. The corpus compounds, so what one run leaves the next
    # one finds. Four minutes, because collection was the longest phase of
    # every run and the reader's wait starts here.
    ingestion_deadline_seconds: int = 240
    # Full-power (2GB) cap on per-mention LLM work (entity-linking + sentiment)
    # per run, so a heavily-covered subject still finishes in a few minutes. The
    # map-reduce digest reads 100% of the corpus regardless; uncapped mentions
    # are picked up on the next incremental run. Raise for deeper single-run
    # analysis at the cost of longer report times.
    max_llm_mentions: int = 300
    #: How many mentions the narrative clusterer reads, newest first.
    #:
    #: The corpus compounds by design — that is the product — and narrative
    #: building embeds every mention it is given and then runs HDBSCAN over
    #: the result. Both are CPU-bound and superlinear, so this stage grows on
    #: every run while nothing bounds it: a live run sat here for an hour.
    #:
    #: A deadline cannot help, because neither call can be interrupted
    #: part-way. The input is what has to be bounded. Newest-first, because
    #: a report about the last month should cluster the last month.
    narrative_corpus_cap: int = 1200
    # Recurring background ingestion for every tracked politician, so mention
    # history accumulates between report requests instead of each report only
    # seeing what one fetch returns. 0 disables (default: off, since each
    # sweep spends SocialCrawl credits).
    ingestion_refresh_hours: float = 0.0
    # Per-sweep, per-politician SocialCrawl credit cap for scheduled runs.
    scheduled_credit_budget: float = 200.0
    # Optional JSON webhook for early-warning alerts (Slack/Make/WhatsApp bridge).
    alert_webhook_url: str = ""
    # Comma-separated names to track automatically in scheduled sweeps, so
    # likely demo/search targets accumulate data before anyone asks.
    prewarm_names: str = ""
    # AgentReach: free, no-API-key web/social scraping via installed backends
    # (github.com/Panniantong/Agent-Reach). Off by default; enable where the
    # agent-reach CLI is installed and outbound network is open.
    enable_agentreach: bool = False
    # Facebook via facebook-scraper (github.com/kevinzg/facebook-scraper) — free
    # public page/group + comment scraping, Kenya's biggest platform. Off by
    # default; needs `pip install facebook-scraper` and open egress.
    enable_facebook_scraper: bool = False
    facebook_pages: str = ""  # comma-separated page names; empty = built-in defaults
    facebook_cookies_path: str = ""  # optional Netscape cookies.txt for logged-in reach
    # GDELT: free historical global-news search (no key). Closes the >30-day
    # history gap NewsAPI's free tier and point-in-time scrapers can't.
    # ON by default: free, no key, no extra packages — only needs open egress.
    enable_gdelt: bool = True
    # Wayback Machine: free archived-page recovery / historical article slugs.
    # ON by default: free, no key, no extra packages — only needs open egress.
    enable_wayback: bool = True
    # twscrape: free X/Twitter via an account pool. Needs a prepared accounts db.
    enable_twscrape: bool = False
    twscrape_db: str = "accounts.db"
    # Scweet (github.com/Altimis/Scweet): free X/Twitter scraping, no paid API,
    # with multi-account pooling + proxy. Off by default; needs a burner X login
    # + open egress. Runs alongside twscrape (both read the shared creds below).
    enable_scweet: bool = False
    scweet_max_tweets: int = 120          # bounded per-run cap
    # Shared burner X account used by BOTH twscrape and Scweet (X blocks
    # anonymous reads). Supply via env X_USERNAME / X_EMAIL / X_PASSWORD.
    x_username: str = ""
    x_email: str = ""
    x_password: str = ""
    # Full-article extraction (trafilatura, keyless): fetch and read the whole
    # body of every article URL (GDELT/NewsAPI/Wayback headlines become full
    # journalism). ON by default: free, only needs open egress + trafilatura.
    enable_article_text: bool = True
    article_text_max_chars: int = 6000   # per-article body cap fed downstream
    # Max article bodies downloaded per RUN — enforced per run since the
    # collection deadline landed; it had been applied per query, ~30x looser.
    # Raised from 60 with that fix, so a run keeps most of the depth it had
    # while the time it may take is bounded.
    article_text_max_fetch: int = 120
    # Scrapling (BSD-3): retry a blocked fetch with a real Chrome TLS/HTTP2
    # fingerprint via curl_cffi. Kenyan newsrooms behind Cloudflare answer 403
    # to `requests` on fingerprint alone, so without this their bodies are
    # simply absent from the corpus. Costs nothing and adds no browser.
    # How long the analyst fan-out may take before unfinished sections are
    # abandoned. Without a deadline a single hung call — a rate-limited free
    # model, a provider that never answers — blocked the report forever, and
    # the page sat on "Still building this report" indefinitely.
    # How far back a report looks, in days. Was hard-coded to 210 — seven
    # months — so every report was dominated by material half a year old and
    # "what is happening now" was diluted by everything that ever happened.
    # A month is the useful default for a monitoring product; the collector
    # still reaches further back, so the corpus keeps its history.
    report_window_days: int = 30
    # The widest look-back a caller may request, so a stray query cannot ask
    # the pipeline to read five years of corpus.
    report_window_max_days: int = 730
    # Wall-clock deadline over the WHOLE analyst fan-out. Raised from 600 with
    # the per-call budget (llm.OPENAI_COMPATIBLE_TOTAL_BUDGET, 420): a thinking
    # model that legitimately spends five minutes on one call left no room for
    # the analysts behind it, so they were abandoned unstarted and reported as
    # sections that "did not finish" — on a provider that was working.
    #
    # Two constraints, and it has to satisfy both: comfortably ABOVE the
    # per-call budget so one slow call cannot consume the fan-out, and still
    # under a quarter of an hour, because that is a reader waiting.
    analyst_deadline_seconds: int = 720
    # Wall-clock ceiling on a WHOLE report run. The analyst phase has its own
    # budget, but the heavy optional blocks after it — entity/event
    # resolution, the knowledge graph, temporal signals, the sentiment
    # framework, claim verification — had none, and each makes model calls. A
    # live run reached "12/15 sections" and stayed there: not failing, just
    # never coming back. A report that stops early and names what it skipped
    # beats one that never arrives.
    #
    # Twenty minutes, down from thirty: the back office (resolution, the
    # knowledge graph, the claim audit) now runs AFTER delivery and no longer
    # spends this budget, so it bounds only what the reader waits for —
    # collection excluded, which has its own ceiling.
    report_deadline_seconds: int = 1200
    # Ceiling on the back office — resolution, the knowledge graph, the claim
    # audit, the investigator — which now runs AFTER the report is delivered
    # (see `pipeline.run_back_office`). Nobody is waiting on it, so it can be
    # generous; it still has one, because a thread that never returns is a
    # leak whether or not a reader is watching it.
    back_office_deadline_seconds: int = 900
    # Characters of corpus a single analyst reads.
    #
    # This was raised to 160k to fix sections answering from 1.4% of the
    # corpus, then cut back to 100k while a free tier was the backend — 160k
    # is ~40k tokens, and a free model with a small context window or a hard
    # rate cap either refused it or queued it until something upstream gave
    # up. The free tier turned out not to be the only problem (see the budget
    # and breaker fixes in llm.py), and the cut cost real breadth: it is a
    # 37% reduction in what every analyst gets to read.
    #
    # Restored to 160k. Every current paid model has the context for it, and
    # breadth of evidence is the product. Tunable via ANALYST_CORPUS_CHARS for
    # a backend that genuinely cannot take it.
    analyst_corpus_chars: int = 160000
    enable_scrapling: bool = True
    scrapling_impersonate: str = "chrome"
    # Browser tier (camoufox/patchright) that can sit through a Cloudflare
    # interstitial. OFF: needs `scrapling install` and seconds per page.
    enable_scrapling_stealth: bool = False
    # Wikipedia (keyless MediaWiki REST API): authoritative background on the
    # subject + linked entities. ON by default: free, no key, no extra packages.
    # Wikipedia: OFF.
    #
    # Turned off at the client's request, and the reports show why. A live
    # Search report opened on "Alma mater is an allegorical Latin phrase
    # meaning 'nourishing mother'" as the subject summary — a dictionary
    # definition scraped from an infobox link, presented as who the subject
    # is. Another headline carried "(; born 22 September 1957)", the stray
    # semicolon of a stripped infobox template.
    #
    # The deeper problem is that the analysts cannot tell an encyclopedia
    # entry from reporting. A Wikipedia page is a tertiary source written
    # about the whole of a life; this product reports what was published in
    # a window. Mixed into the corpus it reads as the most authoritative item
    # present and pulls the summary toward biography and away from the news.
    enable_wikipedia: bool = False
    # Genuinely-free, keyless, Kenya-relevant sources — the real fix for data
    # starvation when SocialCrawl credits are exhausted. All ON by default: no
    # key, no login, no credits; only need open egress.
    # Discovery layer: a self-hosted SearXNG metasearch instance fanning one
    # query across 200+ engines. This is how obscure and archived pages — the
    # buried 1992 article a fixed connector never sees — enter the corpus.
    # Empty URL disables discovery entirely (graceful, like every other source).
    enable_discovery: bool = True
    searxng_url: str = ""                     # e.g. http://searxng:8080
    discovery_max_results_per_query: int = 30
    discovery_max_fetch: int = 80             # bounded full-text fetches per run
    discovery_timeout: int = 20
    # Documents fed into the deep-reading layer per report. The map-reduce
    # digest chunks whatever it is given, so this bounds run time, not coverage.
    document_corpus_limit: int = 400
    # The primary record. Free, keyless, and the thing that separates an
    # intelligence product from a press summary: "The Standard says he
    # appointed X" is journalism; "Gazette Notice No. 1234 of 3 October
    # appointed X" is evidence. A live report's biggest storyline was about
    # board appointments and the corpus held not one gazette notice.
    enable_kenya_gazette: bool = True   # kenyalaw.org — appointments, revocations
    enable_kenya_law: bool = True       # kenyalaw.org — judgments and rulings
    enable_google_news: bool = True   # Google News RSS (Nation/Standard/Citizen/Star)
    enable_reddit: bool = True        # Reddit public JSON search (+ r/Kenya)
    # Reddit refuses keyless requests from cloud servers ("403 Blocked") — it
    # worked from a laptop and never from Railway. A free "script" app at
    # reddit.com/prefs/apps gives these two; with them the connector signs in
    # (app-only, read-only) and asks oauth.reddit.com instead. Without them it
    # tries the public endpoint once per process window and stops asking.
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    enable_youtube: bool = True       # YouTube via yt-dlp (search + top comments)
    # Per-million-token USD prices for the configured Anthropic model, used to
    # turn real token counts into a real spend figure on the admin dashboard.
    anthropic_price_in: float = 3.0
    anthropic_price_out: float = 15.0
    # Same, for whatever model LLM_PROVIDER=openai_compatible is pointed at.
    # The dashboard priced every run at Anthropic's rates regardless of which
    # backend actually served it, so a run on OpenRouter was reported at 4-20x
    # its real cost — useless to anyone deciding whether they can afford the
    # next one. Defaults are Gemini 3.8 Flash's list price; set LLM_PRICE_IN /
    # LLM_PRICE_OUT when pointing LLM_MODEL somewhere else.
    llm_price_in: float = 0.75
    llm_price_out: float = 3.75

    class Config:
        env_file = ".env"
        # A variable left behind by a removed feature (NEO4J_PASSWORD,
        # INTERNAL_API_TOKEN, SERVE_PRECACHE_FIRST…) must not stop the engine
        # starting. They are ignored, not errors.
        extra = "ignore"

    @property
    def is_local_dev(self) -> bool:
        return self.app_env == "local-dev"

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.is_local_dev:
            return "postgresql+psycopg2://postgres:postgres@postgres:5432/political_intel"
        raise RuntimeError("DATABASE_URL must be set explicitly when APP_ENV is not 'local-dev'")

    def configuration_problems(self) -> list[str]:
        """What is missing, without raising.

        Split out of `validate_for_startup` because the same question has
        two callers with opposite needs. A startup check wants to refuse;
        a health endpoint and a preflight want to REPORT — and the report
        is the one that was missing, so a container ran for days with no
        model credential and said nothing until a report died against
        someone else's 401.
        """
        problems: list[str] = []
        provider = (self.llm_provider or "anthropic").strip().lower()
        if provider == "anthropic" and not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is not set")
        # The stub backend makes no calls at all, so it needs no credential;
        # requiring one would defeat the point of a zero-cost test mode.
        if provider == "openai_compatible":
            if not self.llm_base_url:
                problems.append("LLM_BASE_URL is required when LLM_PROVIDER=openai_compatible")
            if not self.llm_api_key:
                problems.append("LLM_API_KEY is required when LLM_PROVIDER=openai_compatible")
            if not self.llm_model:
                problems.append("LLM_MODEL is required when LLM_PROVIDER=openai_compatible")
        if not self.is_local_dev and not self.database_url:
            problems.append("DATABASE_URL is not set (required outside local-dev)")
        return problems

    def validate_for_startup(self) -> None:
        """The raising form of `configuration_problems`, for a script or a
        test that wants to refuse.

        Deliberately NOT called by the served app: a container that refuses
        to start cannot say why, and Railway would restart it in a loop.
        `/api/health` reports the same problems instead. It used to sit on
        engine/main.py, which no deployment served, and read as a guard that
        had never once run."""
        problems = self.configuration_problems()
        if problems:
            raise RuntimeError("Engine refusing to start: " + "; ".join(problems))


settings = Settings()
