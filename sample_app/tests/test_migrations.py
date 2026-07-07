from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from m8flow_sample_app.db import run_migrations
from m8flow_sample_app.settings import get_settings


def test_migrations_reuse_existing_secret_table_and_foreign_alembic_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sample_app_existing_secret.sqlite"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("M8FLOW_SAMPLE_APP_DATABASE_URL", database_url)
    get_settings.cache_clear()

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table alembic_version (
                    version_num varchar(32) not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into alembic_version(version_num)
                values ('4efc3d8655be')
                """
            )
        )
        connection.execute(
            text(
                """
                create table secret (
                    id integer not null primary key,
                    m8f_tenant_id varchar(255) not null,
                    key varchar(50) not null,
                    value text not null,
                    user_id integer not null,
                    updated_at_in_seconds integer,
                    created_at_in_seconds integer
                )
                """
            )
        )
    engine.dispose()

    run_migrations()

    inspector = inspect(create_engine(database_url))
    assert "secret" in inspector.get_table_names()
    assert "m8flow_sample_app_alembic_version" in inspector.get_table_names()

    verification_engine = create_engine(database_url)
    with verification_engine.begin() as connection:
        assert (
            connection.execute(
                text(
                    "select version_num from "
                    "m8flow_sample_app_alembic_version"
                )
            ).scalar()
            == "20260706_0002"
        )
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar()
            == "4efc3d8655be"
        )
    verification_engine.dispose()
    get_settings.cache_clear()
