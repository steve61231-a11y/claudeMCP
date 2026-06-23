from pydantic_settings import BaseSettings


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

    class Config:
        env_file = ".env"


settings = Settings()
