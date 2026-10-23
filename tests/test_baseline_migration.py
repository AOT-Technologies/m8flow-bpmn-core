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

from m8flow_core.models import Base
from m8flow_core.models.user import UserModel
from m8flow_core.settings import get_settings

BASELINE_REVISION = "k2l3m4n5o6p7"

POSTGRES_URL = os.getenv("M8FLOW_POSTGRES_UPGRADE_DATABASE_URL", "")

NAMED_CHECK_CONSTRAINTS = {
    "permission_target": {
        "m8f_permission_target_resource_type_check",
        "m8f_permission_target_resource_pair_check",
    },
    "principal": {"m8f_principal_exactly_one_subject"},
    "process_instance_event": {"m8f_process_instance_event_category_check"},
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


def test_migration_history_is_a_single_baseline() -> None:
    script = ScriptDirectory.from_config(_alembic_config())

    revisions = list(script.walk_revisions())

    assert [revision.revision for revision in revisions] == [BASELINE_REVISION]
    assert script.get_heads() == [BASELINE_REVISION]
    assert revisions[0].down_revision is None


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
                == BASELINE_REVISION
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
