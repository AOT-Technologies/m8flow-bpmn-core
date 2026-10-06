"""Remove legacy epoch timestamp columns.

The timezone-aware datetime columns added by ``d2e4f6a8b0c1`` are now the
canonical persistence fields. This migration is intentionally breaking for
consumers that still read or write ``*_in_seconds`` columns.

Revision ID: f7a8b9c0d1e2
Revises: e1f2a3b4c5d6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "e1f2a3b4c5d6"
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


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name, column_names in LEGACY_EPOCH_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        legacy_names = set(column_names)
        for index in inspector.get_indexes(table_name):
            if legacy_names.intersection(index.get("column_names", ())):
                op.drop_index(index["name"], table_name=table_name)
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            for column_name in column_names:
                batch_op.drop_column(column_name)


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
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            for column_name, column_type in columns.items():
                batch_op.add_column(sa.Column(column_name, column_type, nullable=True))

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
