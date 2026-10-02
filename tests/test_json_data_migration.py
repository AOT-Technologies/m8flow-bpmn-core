from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa


def _load_phase4_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "e5f6a7b8c9d0_phase4_compatibility.py"
    )
    spec = importlib.util.spec_from_file_location("phase4_migration", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load phase 4 migration")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _BatchAlterNoOp:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def drop_constraint(self, *args, **kwargs):
        pass

    def create_foreign_key(self, *args, **kwargs):
        pass

    def create_primary_key(self, *args, **kwargs):
        pass

    def alter_column(self, *args, **kwargs):
        pass


class _MigrationOperations:
    def __init__(self, connection):
        self.connection = connection

    def add_column(self, table_name, column):
        self.connection.execute(
            sa.text(
                f"ALTER TABLE {table_name} ADD COLUMN {column.name} VARCHAR(255)"
            )
        )

    def batch_alter_table(self, *args, **kwargs):
        return _BatchAlterNoOp()

    def get_bind(self):
        return self.connection

    def create_table(self, table_name, *args, **kwargs):
        self.connection.execute(
            sa.text(
                f"CREATE TABLE {table_name} ("
                "m8f_tenant_id VARCHAR(255) NOT NULL, "
                "hash VARCHAR(255) NOT NULL, "
                "data JSON NOT NULL, "
                "PRIMARY KEY (m8f_tenant_id, hash))"
            )
        )

    def drop_table(self, table_name):
        self.connection.execute(sa.text(f"DROP TABLE {table_name}"))

    def rename_table(self, old_name, new_name):
        self.connection.execute(
            sa.text(f"ALTER TABLE {old_name} RENAME TO {new_name}")
        )


def _create_legacy_tables(connection) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "m8flow_tenant",
        metadata,
        sa.Column("id", sa.String(255), primary_key=True),
    )
    sa.Table(
        "json_data",
        metadata,
        sa.Column("hash", sa.String(255), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    sa.Table(
        "bpmn_process",
        metadata,
        sa.Column("m8f_tenant_id", sa.String(255), nullable=False),
        sa.Column("json_data_hash", sa.String(255), nullable=False),
    )
    sa.Table(
        "task",
        metadata,
        sa.Column("m8f_tenant_id", sa.String(255), nullable=False),
        sa.Column("json_data_hash", sa.String(255), nullable=False),
        sa.Column("python_env_data_hash", sa.String(255), nullable=False),
    )
    metadata.create_all(connection)


def test_json_backfill_keeps_shared_and_task_only_payloads(monkeypatch) -> None:
    migration = _load_phase4_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        connection.execute(
            sa.text("INSERT INTO m8flow_tenant (id) VALUES ('tenant-a'), ('tenant-b')")
        )
        connection.execute(
            sa.text("INSERT INTO json_data (hash, data) VALUES (:hash, :data)"),
            [{"hash": "shared", "data": '{"value": "shared"}'},
             {"hash": "task-only", "data": '{"value": "task"}'}],
        )
        connection.execute(
            sa.text(
                "INSERT INTO bpmn_process "
                "(m8f_tenant_id, json_data_hash) VALUES ('tenant-a', 'shared')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO task "
                "(m8f_tenant_id, json_data_hash, python_env_data_hash) "
                "VALUES ('tenant-b', 'shared', 'task-only')"
            )
        )
        monkeypatch.setattr(migration, "op", _MigrationOperations(connection))

        migration._tenant_scope_json_data()

        rows = connection.execute(
            sa.text(
                "SELECT m8f_tenant_id, hash "
                "FROM json_data ORDER BY hash, m8f_tenant_id"
            )
        ).all()
        assert rows == [
            ("tenant-a", "shared"),
            ("tenant-b", "shared"),
            ("tenant-b", "task-only"),
        ]


def test_json_backfill_rejects_unresolvable_references_before_rekeying(
    monkeypatch,
) -> None:
    migration = _load_phase4_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _create_legacy_tables(connection)
        connection.execute(
            sa.text("INSERT INTO m8flow_tenant (id) VALUES ('tenant-a')")
        )
        connection.execute(
            sa.text("INSERT INTO json_data (hash, data) VALUES ('present', '{}')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO bpmn_process "
                "(m8f_tenant_id, json_data_hash) VALUES ('tenant-a', 'missing')"
            )
        )
        monkeypatch.setattr(migration, "op", _MigrationOperations(connection))

        with pytest.raises(RuntimeError, match="missing from json_data"):
            migration._tenant_scope_json_data()

        assert connection.execute(
            sa.text("SELECT m8f_tenant_id FROM json_data WHERE hash = 'present'")
        ).scalar_one() is None
