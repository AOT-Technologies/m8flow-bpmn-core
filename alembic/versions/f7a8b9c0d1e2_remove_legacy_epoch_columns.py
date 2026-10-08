"""Remove legacy epoch timestamp columns.

The timezone-aware datetime columns added by ``d2e4f6a8b0c1`` are now the
canonical persistence fields. This migration is intentionally breaking for
consumers that still read or write ``*_in_seconds`` columns.

Revision ID: f7a8b9c0d1e2
Revises: h9i0j1k2l3m4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "h9i0j1k2l3m4"
branch_labels = None
depends_on = None


LEGACY_EPOCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "user": ("created_at_in_seconds", "updated_at_in_seconds"),
    "m8flow_tenant": ("created_at_in_seconds", "updated_at_in_seconds"),
    "bpmn_process_definition": (
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "bpmn_process": ("start_in_seconds", "end_in_seconds"),
    "task_definition": ("created_at_in_seconds", "updated_at_in_seconds"),
    "task": ("start_in_seconds", "end_in_seconds"),
    "process_instance": (
        "start_in_seconds",
        "end_in_seconds",
        "task_updated_at_in_seconds",
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "human_task": ("created_at_in_seconds", "updated_at_in_seconds"),
    "work_item": ("created_at_in_seconds", "updated_at_in_seconds"),
    "future_task": (
        "run_at_in_seconds",
        "queued_to_run_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "process_instance_metadata": (
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
    "process_instance_event": ("timestamp",),
    "process_model_bpmn_version": ("created_at_in_seconds",),
    "scheduler_job": (
        "locked_at_in_seconds",
        "run_at_in_seconds",
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ),
}

LEGACY_BREAKING_COLUMNS: dict[str, tuple[str, ...]] = {
    "user": (
        "tenant_specific_field_1",
        "tenant_specific_field_2",
        "tenant_specific_field_3",
    ),
    "process_instance": ("spiff_serializer_version",),
    "bpmn_process_definition": (
        "single_process_hash",
        "full_process_model_hash",
    ),
    "permission_target": ("uri",),
}


def _remove_legacy_human_task_schema(bind: sa.engine.Connection) -> None:
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "work_item" not in tables:
        return

    work_item_columns = {
        column["name"] for column in inspector.get_columns("work_item")
    }
    if "task_id" in work_item_columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("work_item", recreate="always") as batch_op:
                batch_op.drop_constraint(
                    "m8f_work_item_human_task_fk", type_="foreignkey"
                )
                batch_op.drop_column("task_id")
                batch_op.create_unique_constraint(
                    "m8f_work_item_task_guid_key", ["task_guid"]
                )
        else:
            for foreign_key in inspector.get_foreign_keys("work_item"):
                if foreign_key.get("referred_table") == "human_task":
                    op.drop_constraint(
                        foreign_key["name"], "work_item", type_="foreignkey"
                    )
            op.drop_column("work_item", "task_id")
            op.create_unique_constraint(
                "m8f_work_item_task_guid_key", "work_item", ["task_guid"]
            )

    if "human_task_user" in tables:
        op.drop_table("human_task_user")
    if "human_task" in tables:
        op.drop_table("human_task")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name, column_names in LEGACY_EPOCH_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        legacy_names = set(column_names)
        for index in inspector.get_indexes(table_name):
            if legacy_names.intersection(index.get("column_names", ())):
                op.drop_index(index["name"], table_name=table_name)
        if table_name == "permission_target":
            existing_constraints = {
                item.get("name")
                for item in sa.inspect(op.get_bind()).get_unique_constraints(
                    table_name
                )
            }
            if (
                op.get_bind().dialect.name != "sqlite"
                and "m8f_permission_target_uri_command_key" in existing_constraints
            ):
                op.drop_constraint(
                    "m8f_permission_target_uri_command_key",
                    table_name,
                    type_="unique",
                )
            for index in sa.inspect(op.get_bind()).get_indexes(table_name):
                if "uri" in index.get("column_names", ()):
                    op.drop_index(index["name"], table_name=table_name)
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table_name, recreate="always") as batch_op:
                for column_name in column_names:
                    batch_op.drop_column(column_name)
        else:
            for column_name in column_names:
                op.drop_column(table_name, column_name)

    for table_name, column_names in LEGACY_BREAKING_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        columns_to_drop = [
            column_name
            for column_name in column_names
            if column_name in existing_columns
        ]
        if not columns_to_drop:
            continue
        if table_name == "permission_target":
            existing_constraints = {
                item.get("name")
                for item in sa.inspect(op.get_bind()).get_unique_constraints(
                    table_name
                )
            }
            if (
                op.get_bind().dialect.name != "sqlite"
                and "m8f_permission_target_uri_command_key" in existing_constraints
            ):
                op.drop_constraint(
                    "m8f_permission_target_uri_command_key",
                    table_name,
                    type_="unique",
                )
            for index in sa.inspect(op.get_bind()).get_indexes(table_name):
                if "uri" in index.get("column_names", ()):
                    op.drop_index(index["name"], table_name=table_name)
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table_name, recreate="always") as batch_op:
                if table_name == "permission_target":
                    batch_op.alter_column("command", nullable=False)
                    batch_op.alter_column("resource_type", nullable=False)
                for column_name in columns_to_drop:
                    batch_op.drop_column(column_name)
        else:
            if table_name == "permission_target":
                op.alter_column("permission_target", "command", nullable=False)
                op.alter_column("permission_target", "resource_type", nullable=False)
            if table_name == "bpmn_process_definition":
                for constraint_name in (
                    "m8f_bpmn_process_definition_full_process_model_hash_tenant_key",
                    "m8f_bpmn_process_definition_process_hash_key",
                ):
                    existing_constraints = {
                        item.get("name")
                        for item in sa.inspect(op.get_bind()).get_unique_constraints(
                            table_name
                        )
                    }
                    if constraint_name in existing_constraints:
                        op.drop_constraint(
                            constraint_name, table_name, type_="unique"
                        )
            for column_name in columns_to_drop:
                op.drop_column(table_name, column_name)

    _remove_legacy_human_task_schema(op.get_bind())


def downgrade() -> None:
    """Restore empty BIGINT columns only.

    Values removed by the upgrade cannot be reconstructed. Restore a backup
    instead when legacy values are required.
    """

    column_types: dict[str, dict[str, sa.types.TypeEngine]] = {
        "user": {
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "m8flow_tenant": {
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "bpmn_process_definition": {
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "bpmn_process": {
            "start_in_seconds": sa.Numeric(17, 6),
            "end_in_seconds": sa.Numeric(17, 6),
        },
        "task_definition": {
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "task": {
            "start_in_seconds": sa.Numeric(17, 6),
            "end_in_seconds": sa.Numeric(17, 6),
        },
        "process_instance": {
            "start_in_seconds": sa.BigInteger(),
            "end_in_seconds": sa.BigInteger(),
            "task_updated_at_in_seconds": sa.BigInteger(),
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "human_task": {
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "work_item": {
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "future_task": {
            "run_at_in_seconds": sa.BigInteger(),
            "queued_to_run_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "process_instance_metadata": {
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
        "process_instance_event": {"timestamp": sa.Numeric(17, 6)},
        "process_model_bpmn_version": {
            "created_at_in_seconds": sa.BigInteger(),
        },
        "scheduler_job": {
            "locked_at_in_seconds": sa.BigInteger(),
            "run_at_in_seconds": sa.BigInteger(),
            "created_at_in_seconds": sa.BigInteger(),
            "updated_at_in_seconds": sa.BigInteger(),
        },
    }

    for table_name, columns in reversed(tuple(column_types.items())):
        if table_name == "human_task" and not sa.inspect(op.get_bind()).has_table(
            table_name
        ):
            # The breaking migration permanently removes human_task.  A
            # downgrade can restore epoch columns on surviving tables, but it
            # cannot recreate a retired table or recover its deleted rows.
            continue
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table_name, recreate="always") as batch_op:
                for column_name, column_type in columns.items():
                    batch_op.add_column(
                        sa.Column(column_name, column_type, nullable=True)
                    )
        else:
            for column_name, column_type in columns.items():
                op.add_column(
                    table_name,
                    sa.Column(column_name, column_type, nullable=True),
                )

    for table_name, index_name, column_names in (
        (
            "process_instance",
            "ix_process_instance_start_in_seconds",
            ("start_in_seconds",),
        ),
        (
            "process_instance",
            "ix_process_instance_end_in_seconds",
            ("end_in_seconds",),
        ),
        (
            "future_task",
            "ix_future_task_run_at_in_seconds",
            ("run_at_in_seconds",),
        ),
        (
            "future_task",
            "ix_future_task_queued_to_run_at_in_seconds",
            ("queued_to_run_at_in_seconds",),
        ),
        (
            "process_model_bpmn_version",
            "ix_process_model_bpmn_version_created_at_in_seconds",
            ("created_at_in_seconds",),
        ),
        (
            "scheduler_job",
            "ix_scheduler_job_locked_at_in_seconds",
            ("locked_at_in_seconds",),
        ),
        (
            "scheduler_job",
            "ix_scheduler_job_run_at_in_seconds",
            ("run_at_in_seconds",),
        ),
        (
            "process_instance_event",
            "ix_process_instance_event_timestamp",
            ("timestamp",),
        ),
    ):
        op.create_index(index_name, table_name, list(column_names))
