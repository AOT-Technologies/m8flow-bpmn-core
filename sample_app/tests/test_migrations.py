from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from m8flow_sample_app.db import build_alembic_config, run_migrations
from m8flow_sample_app.models import SecretModel
from m8flow_sample_app.settings import get_settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from m8flow_core.models import (
    Base,
    M8flowTenantModel,
    SignalPublicationModel,
    UserModel,
)

SIGNAL_TABLES = {
    "signal_registry_state",
    "signal_publication",
    "signal_subscription",
    "signal_delivery",
}
COMPENSATION_TABLES = {"compensation_scope", "compensation_run", "compensation_record"}
COLLABORATION_REVISION = "20261112_0005"
COLLABORATION_INDEX = "ix_process_instance_collaboration_lookup"
COLLABORATION_FK = "fk_process_instance_definition_tenant"
PARENT_INDEXES = {
    "process_instance": "m8f_process_instance_tenant_id_key",
    "bpmn_process_definition": "m8f_definition_tenant_id_key",
}


@pytest.fixture()
def migration_database(monkeypatch, tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'signals.sqlite'}"
    monkeypatch.setenv("M8FLOW_SAMPLE_APP_DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_engine(url)
    try:
        yield engine, build_alembic_config(url)
    finally:
        engine.dispose()
        get_settings.cache_clear()


def assert_signal_schema(engine) -> None:
    inspector = inspect(engine)
    assert SIGNAL_TABLES <= set(inspector.get_table_names())
    for table, name in PARENT_INDEXES.items():
        indexes = {index["name"]: index for index in inspector.get_indexes(table)}
        assert indexes[name]["unique"]
        assert indexes[name]["column_names"] == ["m8f_tenant_id", "id"]
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": lambda obj, name, kind, reflected, counterpart: (
                    kind != "table" or name in SIGNAL_TABLES
                ),
                "version_table": "m8flow_sample_app_alembic_version",
            },
        )
        assert compare_metadata(context, Base.metadata) == []


def test_upgrade_from_signal_revision_adds_compensation_schema(migration_database):
    engine, config = migration_database
    command.upgrade(config, "20261104_0003")
    assert COMPENSATION_TABLES.isdisjoint(inspect(engine).get_table_names())

    command.upgrade(config, "head")

    assert_signal_schema(engine)
    inspector = inspect(engine)
    assert COMPENSATION_TABLES <= set(inspector.get_table_names())
    for table in COMPENSATION_TABLES:
        assert any(
            fk["constrained_columns"] == ["m8f_tenant_id", "process_instance_id"]
            and fk["referred_table"] == "process_instance"
            and fk["referred_columns"] == ["m8f_tenant_id", "id"]
            for fk in inspector.get_foreign_keys(table)
        )


def test_fresh_migration_creates_signal_schema_and_is_repeatable(migration_database):
    engine, config = migration_database
    command.upgrade(config, "head")
    assert_signal_schema(engine)
    command.upgrade(config, "head")
    assert_signal_schema(engine)


def test_upgrade_existing_sample_app_adds_collaboration_schema(migration_database):
    engine, config = migration_database
    command.upgrade(config, "head")
    # Downgrading removes the objects that an older ORM baseline did not create.
    command.downgrade(config, "20261110_0004")
    inspector = inspect(engine)
    assert "collaboration_correlation_key" not in {
        column["name"] for column in inspector.get_columns("process_instance")
    }
    assert COLLABORATION_INDEX not in {
        index["name"] for index in inspector.get_indexes("process_instance")
    }
    assert COLLABORATION_FK not in {
        key["name"] for key in inspector.get_foreign_keys("process_instance")
    }

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert any(
        column["name"] == "collaboration_correlation_key"
        and column["nullable"]
        and column["type"].length == 255
        for column in inspector.get_columns("process_instance")
    )
    assert any(
        index["name"] == COLLABORATION_INDEX
        and index["column_names"] == [
            "m8f_tenant_id", "bpmn_process_definition_id", "bpmn_version_id",
            "collaboration_correlation_key",
        ]
        for index in inspector.get_indexes("process_instance")
    )
    assert any(
        key["name"] == COLLABORATION_FK
        and key["constrained_columns"] == ["m8f_tenant_id", "bpmn_process_definition_id"]
        and key["referred_table"] == "bpmn_process_definition"
        and key["referred_columns"] == ["m8f_tenant_id", "id"]
        for key in inspector.get_foreign_keys("process_instance")
    )


def test_upgrade_from_previous_head_adds_indexes_and_preserves_data(migration_database):
    from datetime import UTC, datetime

    engine, config = migration_database
    command.upgrade(config, "20260706_0002")
    assert SIGNAL_TABLES.isdisjoint(inspect(engine).get_table_names())
    with engine.begin() as connection:
        # Older hosts do not have the composite indexes added by signal support.
        for name in PARENT_INDEXES.values():
            connection.exec_driver_sql(f'DROP INDEX "{name}"')
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('host-owned')"))
    with Session(engine) as session:
        tenant = M8flowTenantModel(id="preserved", name="Preserved", slug="preserved")
        user = UserModel(
            username="preserved",
            service="app/realms/preserved",
            service_id="user",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add_all([tenant, user])
        session.flush()
        session.add(
            SecretModel(
                m8f_tenant_id=tenant.id,
                key="preserved",
                value="keep me",
                user_id=user.id,
            )
        )
        session.commit()
    command.upgrade(config, "head")
    assert_signal_schema(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT value FROM secret")) == "keep me"
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "host-owned"
        )
        assert (
            connection.scalar(
                text("SELECT version_num FROM m8flow_sample_app_alembic_version")
            )
            == COLLABORATION_REVISION
        )


def test_existing_signal_tables_and_publications_are_preserved(migration_database):
    from datetime import UTC, datetime

    engine, config = migration_database
    command.upgrade(config, "20260706_0002")
    # A shared host may already have applied the core signal migration.
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_signal_delivery_due")
    with Session(engine) as session:
        session.add(M8flowTenantModel(id="shared", name="Shared", slug="shared"))
        session.flush()
        publication = SignalPublicationModel(
            m8f_tenant_id="shared",
            occurrence_id="existing",
            signal_name="Notice",
            payload_json={"preserve": True},
            published_at=datetime.now(UTC),
        )
        session.add(publication)
        session.commit()
        publication_id = publication.id
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert_signal_schema(engine)
    with Session(engine) as session:
        publication = session.get(SignalPublicationModel, publication_id)
        assert publication.occurrence_id == "existing"
        assert publication.payload_json == {"preserve": True}


@pytest.mark.parametrize(
    ("schema_statements", "invalid_object"),
    [
        (
            ("CREATE TABLE signal_publication (id INTEGER NOT NULL PRIMARY KEY)",),
            "signal_publication.*column occurrence_id",
        ),
        (
            (
                "DROP INDEX m8f_process_instance_tenant_id_key",
                "CREATE INDEX m8f_process_instance_tenant_id_key ON process_instance (id)",
            ),
            "process_instance.*index m8f_process_instance_tenant_id_key",
        ),
    ],
    ids=["partial-table", "incompatible-index"],
)
def test_incompatible_existing_signal_schema_is_rejected(
    migration_database, schema_statements, invalid_object
):
    engine, config = migration_database
    command.upgrade(config, "20260706_0002")
    with engine.begin() as connection:
        for statement in schema_statements:
            connection.exec_driver_sql(statement)

    with pytest.raises(RuntimeError, match=invalid_object):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT version_num FROM m8flow_sample_app_alembic_version")
            )
            == "20260706_0002"
        )


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
    assert {
        "secret",
        "work_item",
        "compensation_scope",
        "compensation_run",
        "compensation_record",
        "m8flow_sample_app_alembic_version",
    }.issubset(inspector.get_table_names())

    verification_engine = create_engine(database_url)
    with verification_engine.begin() as connection:
        assert (
            connection.execute(
                text("select version_num from m8flow_sample_app_alembic_version")
            ).scalar()
            == COLLABORATION_REVISION
        )
        assert (
            connection.execute(text("select version_num from alembic_version")).scalar()
            == "4efc3d8655be"
        )
    verification_engine.dispose()
    get_settings.cache_clear()
