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
    # "local-dev" enables convenience fallbacks (default DB creds, optional
    # internal token). Anything else fails closed: explicit credentials and
    # INTERNAL_API_TOKEN become mandatory (validated at engine startup).
    app_env: str = "local-dev"
    database_url: str = ""
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
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
    # Investigator: after the report, name what is missing and turn those gaps
    # into queries the next run chases. This is what makes successive runs an
    # investigation rather than a repeated sweep.
    enable_investigator: bool = True
    sentiment_confidence_threshold: float = 0.55
    internal_api_token: str = ""
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
    # Demo/degraded-infra mode: serve the pre-generated report immediately for
    # matched names instead of running the (currently memory-blocked) live
    # pipeline. Instant, spends zero LLM credits, and shows the full original
    # report. Turn OFF (default) once the engine has enough RAM to run live.
    serve_precache_first: bool = False
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
    # Full-power (2GB) cap on per-mention LLM work (entity-linking + sentiment)
    # per run, so a heavily-covered subject still finishes in a few minutes. The
    # map-reduce digest reads 100% of the corpus regardless; uncapped mentions
    # are picked up on the next incremental run. Raise for deeper single-run
    # analysis at the cost of longer report times.
    max_llm_mentions: int = 300
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
    article_text_max_fetch: int = 60      # max URLs enriched per run (bounded)
    # Scrapling (BSD-3): retry a blocked fetch with a real Chrome TLS/HTTP2
    # fingerprint via curl_cffi. Kenyan newsrooms behind Cloudflare answer 403
    # to `requests` on fingerprint alone, so without this their bodies are
    # simply absent from the corpus. Costs nothing and adds no browser.
    # How long the analyst fan-out may take before unfinished sections are
    # abandoned. Without a deadline a single hung call — a rate-limited free
    # model, a provider that never answers — blocked the report forever, and
    # the page sat on "Still building this report" indefinitely.
    analyst_deadline_seconds: int = 900
    # Characters of corpus a single analyst reads. Raised to 160k to fix
    # sections answering from 1.4% of the corpus, which was real — but 160k is
    # ~40k tokens, and a free-tier model with a small context window or a hard
    # rate cap either refuses it or queues it until something upstream gives
    # up. Tunable so a thin free model and a paid one with room can each be
    # served without a code change.
    analyst_corpus_chars: int = 100000
    enable_scrapling: bool = True
    scrapling_impersonate: str = "chrome"
    # Browser tier (camoufox/patchright) that can sit through a Cloudflare
    # interstitial. OFF: needs `scrapling install` and seconds per page.
    enable_scrapling_stealth: bool = False
    # Wikipedia (keyless MediaWiki REST API): authoritative background on the
    # subject + linked entities. ON by default: free, no key, no extra packages.
    enable_wikipedia: bool = True
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
    enable_google_news: bool = True   # Google News RSS (Nation/Standard/Citizen/Star)
    enable_reddit: bool = True        # Reddit public JSON search (+ r/Kenya)
    enable_youtube: bool = True       # YouTube via yt-dlp (search + top comments)
    # Per-million-token USD prices for the configured Anthropic model, used to
    # turn real token counts into a real spend figure on the admin dashboard.
    anthropic_price_in: float = 3.0
    anthropic_price_out: float = 15.0

    class Config:
        env_file = ".env"

    @property
    def is_local_dev(self) -> bool:
        return self.app_env == "local-dev"

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.is_local_dev:
            return "postgresql+psycopg2://postgres:postgres@postgres:5432/political_intel"
        raise RuntimeError("DATABASE_URL must be set explicitly when APP_ENV is not 'local-dev'")

    def resolved_neo4j_password(self) -> str:
        if self.neo4j_password:
            return self.neo4j_password
        if self.is_local_dev:
            return "password"
        raise RuntimeError("NEO4J_PASSWORD must be set explicitly when APP_ENV is not 'local-dev'")

    def validate_for_startup(self) -> None:
        """Fail-closed startup checks: outside local-dev, refuse to run with
        missing shared secrets or credential fallbacks."""
        problems = []
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
        if not self.is_local_dev:
            if not self.internal_api_token:
                problems.append("INTERNAL_API_TOKEN is not set (required outside local-dev)")
            if not self.database_url:
                problems.append("DATABASE_URL is not set (required outside local-dev)")
            if not self.neo4j_password:
                problems.append("NEO4J_PASSWORD is not set (required outside local-dev)")
        if problems:
            raise RuntimeError("Engine refusing to start: " + "; ".join(problems))


settings = Settings()
