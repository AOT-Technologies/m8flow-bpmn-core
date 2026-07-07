from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="M8FLOW_SAMPLE_APP_",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:6843/postgres"
    )
    database_echo: bool = False
    secret_key: str = "sample-app-dev-secret"
    host: str = "127.0.0.1"
    port: int = 5010
    debug: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
