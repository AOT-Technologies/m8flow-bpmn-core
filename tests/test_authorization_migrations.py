from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from m8flow_bpmn_core.settings import get_settings


def _alembic_config() -> Config:
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    return config


def test_full_migration_chain_reaches_head_on_sqlite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'full-chain.db'}"
    monkeypatch.setenv("M8FLOW_DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        command.upgrade(_alembic_config(), "head")
        engine = sa.create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert connection.scalar(
                    sa.text("SELECT version_num FROM alembic_version")
                ) == "d0e1f2a3b4c5"
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def test_authorization_migrations_preserve_legacy_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setenv("M8FLOW_DATABASE_URL", database_url)
    get_settings.cache_clear()

    config = _alembic_config()
    command.stamp(config, "f6a7b8c9d0e1")

    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            legacy_metadata = sa.MetaData()
            sa.Table(
                "group",
                legacy_metadata,
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("name", sa.String(length=255)),
                sa.Column("identifier", sa.String(length=255)),
                sa.Column("source_is_open_id", sa.Boolean(), nullable=False),
            )
            sa.Table(
                "principal",
                legacy_metadata,
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("user_id", sa.Integer(), nullable=True),
                sa.Column("group_id", sa.Integer(), nullable=True),
                sa.CheckConstraint(
                    (
                        "(user_id IS NOT NULL AND group_id IS NULL) OR "
                        "(user_id IS NULL AND group_id IS NOT NULL)"
                    ),
                    name="principal_exactly_one_subject",
                ),
            )
            sa.Table(
                "permission_target",
                legacy_metadata,
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("uri", sa.String(length=255), nullable=False),
                sa.Column("command", sa.String(length=255), nullable=True),
                sa.Column("resource_type", sa.String(length=100), nullable=True),
                sa.Column("resource_id", sa.String(length=255), nullable=True),
            )
            legacy_metadata.create_all(connection)
            connection.execute(
                sa.text(
                    "INSERT INTO \"group\" "
                    "(name, identifier, source_is_open_id) "
                    "VALUES (:name, :identifier, :source_is_open_id)"
                ),
                {
                    "name": "Legacy managers",
                    "identifier": "tenant-a:manager",
                    "source_is_open_id": False,
                },
            )
            group_id = connection.scalar(
                sa.text(
                    "SELECT id FROM \"group\" "
                    "WHERE identifier = 'tenant-a:manager'"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO principal (user_id, group_id) "
                    "VALUES (NULL, :group_id)"
                ),
                {"group_id": group_id},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO permission_target (uri, command) "
                    "VALUES (:uri, :command)"
                ),
                {"uri": "/tasks/%", "command": "task.claim"},
            )

        command.upgrade(config, "head")

        inspector = inspect(engine)
        group_columns = {column["name"] for column in inspector.get_columns("group")}
        target_columns = {
            column["name"]
            for column in inspector.get_columns("permission_target")
        }
        assert "authorization_key" in group_columns
        assert {"resource_type", "resource_id"} <= target_columns
        assert "m8f_group_authorization_key" in {
            item.get("name") for item in inspector.get_unique_constraints("group")
        }
        assert "m8f_principal_exactly_one_subject" in {
            item.get("name") for item in inspector.get_check_constraints("principal")
        }

        with engine.connect() as connection:
            legacy_target = connection.execute(
                sa.text(
                    "SELECT uri, command, resource_type, resource_id "
                    "FROM permission_target WHERE uri = '/tasks/%'"
                )
            ).one()
            assert tuple(legacy_target) == (
                "/tasks/%",
                "task.claim",
                None,
                None,
            )
            assert connection.scalar(
                sa.text(
                    "SELECT COUNT(*) FROM \"group\" "
                    "WHERE identifier = 'tenant-a:manager'"
                )
            ) == 1
    finally:
        engine.dispose()
        get_settings.cache_clear()
