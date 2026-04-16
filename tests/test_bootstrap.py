from __future__ import annotations

from m8flow_bpmn_core.db import build_engine
from m8flow_bpmn_core.models import Base
from m8flow_bpmn_core.settings import Settings


def test_settings_default_to_sqlite() -> None:
    settings = Settings()

    assert settings.database_url.startswith("sqlite+pysqlite:///")
    assert settings.database_echo is False


def test_build_engine_accepts_in_memory_sqlite() -> None:
    engine = build_engine("sqlite+pysqlite:///:memory:")

    assert engine.url.drivername == "sqlite+pysqlite"
    engine.dispose()


def test_base_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
