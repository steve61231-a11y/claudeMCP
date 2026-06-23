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
    database_url: str = "postgresql+psycopg2://postgres:postgres@postgres:5432/political_intel"
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    sentiment_confidence_threshold: float = 0.55
    internal_api_token: str = ""
    newsapi_key: str = ""
    socialcrawl_api_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
