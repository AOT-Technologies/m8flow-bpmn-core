from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="M8FLOW_", extra="ignore"
    )

    database_url: str = "sqlite+pysqlite:///./m8flow.db"
    database_echo: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
