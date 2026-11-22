from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SupplyWatch"
    app_version: str = "0.2.0-copilot"
    env: str = "dev"
    database_url: str
    resend_api_key: str = ""
    resend_from_email: str = "alerts@supplywatch.local"
    enable_scheduler: bool = True
    default_rate_limit_free: int = 100
    # Optional free-tier price-data fallbacks (used only if yfinance returns
    # nothing usable). Both are no-ops when left blank -- see ingest/usgs.py.
    twelvedata_api_key: str = ""
    finnhub_api_key: str = ""

    # ---- Copilot additions ----
    # LLM backend: auto | anthropic | claude_cli | fake | none
    #   auto = anthropic if ANTHROPIC_API_KEY is set, else claude_cli if the
    #          `claude` binary is on PATH, else none (extractive fallback).
    llm_backend: str = "auto"
    anthropic_api_key: str = ""
    answer_model: str = "claude-haiku-5-5"   # bulk: /ask answers, eval judging
    agent_model: str = "claude-sonnet-5-5"   # LangGraph analyst agent
    llm_cache_dir: str = ".llm_cache"        # on-disk response cache

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    retrieval_mode: str = "rerank"           # vector | hybrid | rerank
    retrieval_top_k: int = 5
    # Minimum cosine similarity of the best chunk; below it /ask refuses.
    refusal_min_similarity: float = 0.62

    # OAuth2 client-credentials
    jwt_secret: str = "change-me-dev-only-secret-at-least-32-bytes"
    jwt_ttl_seconds: int = 900
    admin_bootstrap_secret: str = ""

    # Outgoing webhooks
    webhook_signing_secret: str = "dev-webhook-secret"
    webhook_max_attempts: int = 4
    webhook_backoff_base_seconds: float = 0.5

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
