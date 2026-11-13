#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session

from m8flow_core.models import (
    Base,
    CompensationRecordModel,
    CompensationRunModel,
    CompensationScopeModel,
    M8flowTenantModel,
    ProcessInstanceModel,
    SignalPublicationModel,
)
from m8flow_core.models.user import UserModel
from m8flow_core.settings import get_settings

BASELINE_REVISION = "k2l3m4n5o6p7"
SIGNAL_REVISION = "l3m4n5o6p7q8"
HEAD_REVISION = "m8f_671_compensation"

POSTGRES_URL = os.getenv("M8FLOW_POSTGRES_UPGRADE_DATABASE_URL", "")

NAMED_CHECK_CONSTRAINTS = {
    "permission_target": {
        "m8f_permission_target_resource_type_check",
        "m8f_permission_target_resource_pair_check",
    },
    "principal": {"m8f_principal_exactly_one_subject"},
    "process_instance_event": {"m8f_process_instance_event_category_check"},
    "signal_subscription": {"m8f_signal_subscription_references"},
}


def _alembic_config() -> Config:
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    return config


@pytest.fixture()
def migration_database(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    database_url: str = request.param or (
        f"sqlite+pysqlite:///{tmp_path / 'baseline.db'}"
    )
    monkeypatch.setenv("M8FLOW_DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        yield database_url
    finally:
        get_settings.cache_clear()


def _duplicates_primary_key(diff: object) -> bool:
    # PostgreSQL folds a UNIQUE constraint over exactly the primary-key
    # columns into the primary key, so it never appears on reflection.
    # ``Base.metadata.create_all`` produces the same result.
    if not (isinstance(diff, tuple) and diff[0] == "add_constraint"):
        return False
    constraint = diff[1]
    if not isinstance(constraint, sa.UniqueConstraint):
        return False
    table = Base.metadata.tables[constraint.table.name]
    return set(constraint.columns.keys()) == set(table.primary_key.columns.keys())


def _schema_diff(database_url: str) -> list[object]:
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diffs = compare_metadata(context, Base.metadata)
            if connection.dialect.name == "postgresql":
                diffs = [diff for diff in diffs if not _duplicates_primary_key(diff)]
            return list(diffs)
    finally:
        engine.dispose()


def _table_names(database_url: str) -> set[str]:
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            return set(sa.inspect(connection).get_table_names())
    finally:
        engine.dispose()


def test_migration_history_has_one_head_after_signal_revision() -> None:
    script = ScriptDirectory.from_config(_alembic_config())

    revisions = list(script.walk_revisions())

    assert [revision.revision for revision in revisions] == [
        HEAD_REVISION,
        SIGNAL_REVISION,
        BASELINE_REVISION,
    ]
    assert script.get_heads() == [HEAD_REVISION]
    assert revisions[0].down_revision == SIGNAL_REVISION
    assert revisions[1].down_revision == BASELINE_REVISION
    assert revisions[2].down_revision is None


_DATABASES = [
    pytest.param(None, id="sqlite"),
    pytest.param(
        POSTGRES_URL,
        id="postgresql",
        marks=pytest.mark.skipif(
            not POSTGRES_URL.startswith("postgresql"),
            reason=(
                "set M8FLOW_POSTGRES_UPGRADE_DATABASE_URL to run the "
                "PostgreSQL baseline migration test"
            ),
        ),
    ),
]


@pytest.mark.parametrize("migration_database", _DATABASES, indirect=True)
def test_baseline_upgrade_matches_models_and_downgrade_removes_tables(
    migration_database: str,
) -> None:
    config = _alembic_config()

    command.upgrade(config, "head")

    engine = sa.create_engine(migration_database)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == HEAD_REVISION
            )
            inspector = sa.inspect(connection)
            for table_name, expected in NAMED_CHECK_CONSTRAINTS.items():
                names = {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints(table_name)
                }
                assert expected <= names, table_name
    finally:
        engine.dispose()
    assert _schema_diff(migration_database) == []
    command.check(config)

    command.downgrade(config, "base")

    remaining = _table_names(migration_database)
    assert remaining.isdisjoint(Base.metadata.tables), remaining


@pytest.mark.parametrize("migration_database", _DATABASES, indirect=True)
def test_baseline_schema_round_trips_post_2038_utc_timestamps(
    migration_database: str,
) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")
    engine = sa.create_engine(migration_database)
    try:
        timestamp = datetime(2040, 1, 1, 12, 30, tzinfo=UTC)
        with Session(engine) as session:
            user = UserModel(
                username="baseline-post-2038",
                service="test",
                service_id="baseline-post-2038",
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            assert user.created_at == timestamp
            assert user.updated_at == timestamp
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.mark.parametrize("migration_database", _DATABASES, indirect=True)
def test_additive_upgrade_preserves_baseline_data(migration_database: str) -> None:
    config = _alembic_config()
    command.upgrade(config, BASELINE_REVISION)
    engine = sa.create_engine(migration_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    'INSERT INTO "user" (username, service, service_id) '
                    "VALUES ('existing-user', 'migration-test', 'existing-user')"
                )
            )
        command.upgrade(config, "head")
        assert _schema_diff(migration_database) == []
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text('SELECT username FROM "user" WHERE service_id = :key'),
                    {"key": "existing-user"},
                )
                == "existing-user"
            )
        command.downgrade(config, BASELINE_REVISION)
        assert "compensation_record" not in _table_names(migration_database)
        assert "process_instance" in _table_names(migration_database)
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.mark.parametrize("migration_database", _DATABASES, indirect=True)
def test_upgrade_from_signal_preserves_data_and_enforces_journal_tenant(
    migration_database: str,
) -> None:
    config = _alembic_config()
    command.upgrade(config, SIGNAL_REVISION)
    engine = sa.create_engine(migration_database)
    tenant = M8flowTenantModel.__table__
    instance = ProcessInstanceModel.__table__
    publication = SignalPublicationModel.__table__
    now = datetime.now(UTC)
    journal_rows = (
        (
            CompensationScopeModel.__table__,
            {"activity_id": "saga", "completed": False, "data_hash": "scope-data"},
        ),
        (
            CompensationRunModel.__table__,
            {
                "run_id": "run",
                "throw_task_guid": "throw",
                "wait_for_completion": True,
                "status": "running",
            },
        ),
        (
            CompensationRecordModel.__table__,
            {
                "source_task_guid": "source",
                "activity_id": "shipment",
                "completion_sequence": 1,
                "status": "available",
                "attempts": 0,
                "data_hash": "record-data",
            },
        ),
    )
    if engine.dialect.name == "sqlite":

        @sa.event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, _record) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    try:
        with engine.begin() as connection:
            connection.execute(
                tenant.insert(),
                [{"id": name, "name": name, "slug": name} for name in ("one", "two")],
            )
            user_id = connection.scalar(
                UserModel.__table__.insert()
                .values(username="operator", service="test", service_id="operator")
                .returning(UserModel.__table__.c.id)
            )
            instance_id = connection.scalar(
                instance.insert()
                .values(
                    m8f_tenant_id="one",
                    process_model_identifier="saga",
                    process_model_display_name="Saga",
                    process_initiator_id=user_id,
                )
                .returning(instance.c.id)
            )
            connection.execute(
                publication.insert().values(
                    m8f_tenant_id="one",
                    source_process_instance_id=instance_id,
                    occurrence_id="existing-signal",
                    signal_name="Notice",
                    payload_json={"preserved": True},
                    published_at=now,
                )
            )

        command.upgrade(config, "head")
        assert _schema_diff(migration_database) == []
        with engine.begin() as connection:
            for table, values in journal_rows:
                insert = table.insert().values(
                    process_instance_id=instance_id,
                    scope_id="scope",
                    created_at=now,
                    updated_at=now,
                    **values,
                )
                connection.execute(insert.values(m8f_tenant_id="one"))
                with (
                    pytest.raises(sa.exc.IntegrityError, match=r"(?i)foreign key"),
                    connection.begin_nested(),
                ):
                    connection.execute(insert.values(m8f_tenant_id="two"))
                count = connection.scalar(sa.select(sa.func.count()).select_from(table))
                assert count == 1

        command.downgrade(config, SIGNAL_REVISION)
        assert {table.name for table, _ in journal_rows}.isdisjoint(
            _table_names(migration_database)
        )
        with engine.connect() as connection:
            assert connection.scalar(sa.select(publication.c.payload_json)) == {
                "preserved": True
            }
            assert connection.scalar(sa.select(instance.c.id)) == instance_id
    finally:
        engine.dispose()
        command.downgrade(config, "base")
