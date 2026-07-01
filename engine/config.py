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
    sentiment_confidence_threshold: float = 0.55
    internal_api_token: str = ""
    newsapi_key: str = ""
    socialcrawl_api_key: str = ""

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
        if not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is not set")
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
