"""Typed application configuration, loaded from `.env` and the process environment."""

import logging
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application configuration. Field names must match `.env.example` exactly."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    beeper_access_token: SecretStr = SecretStr("")
    beeper_api_url: str = "http://127.0.0.1:23373"
    beeper_chat_id: str = ""

    ollama_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "bge-m3"
    extraction_model: str = "qwen3:8b"

    vault_path: Path | None = None
    vault_exclude: Annotated[tuple[str, ...], NoDecode] = ("wiki", ".obsidian")

    backfill_start_date: date | None = None

    fetch_backend: Literal["firecrawl", "direct"] = "firecrawl"
    firecrawl_api_key: SecretStr = SecretStr("")
    fetch_cache_dir: Path = Path(".cache/fetch")
    max_fetches_per_run: int = Field(default=50, gt=0)

    api_token: SecretStr = SecretStr("")
    database_path: Path = Path("data/garden.db")

    @field_validator("vault_exclude", mode="before")
    @classmethod
    def _split_vault_exclude(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @property
    def effective_fetch_backend(self) -> Literal["firecrawl", "direct"]:
        if self.fetch_backend == "firecrawl" and not self.firecrawl_api_key:
            return "direct"
        return self.fetch_backend


def load_settings() -> Settings:
    """Load settings from `.env` and the environment, warning on an unsafe fetch fallback."""
    settings = Settings()
    if settings.fetch_backend == "firecrawl" and settings.effective_fetch_backend == "direct":
        logger.warning(
            "FIRECRAWL_API_KEY is empty. Falling back to direct fetching, "
            "which sends requests from this machine's IP."
        )
    return settings
