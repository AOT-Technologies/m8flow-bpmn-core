from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AuditMode = Literal["auto", "off", "shared"]


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
    m8flow_audit_mode: AuditMode = "auto"
    m8flow_shared_database_name: str = "postgres"
    m8flow_backend_process_models_dir: str | None = None
    m8flow_backend_process_models_target: str = "/app/data/process_models"
    m8flow_backend_container_names: str = (
        "m8flow-m8flow-backend-1,m8flow-backend,m8flow-backend-1"
    )
    m8flow_backend_tenant_root: str | None = None
    keycloak_login_client_id: str = "m8flow-sample-app"
    keycloak_login_public_base_urls: str | None = None
    connector_proxy_base_url: str = "http://localhost:6844"
    connector_proxy_timeout_seconds: float = 10.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
