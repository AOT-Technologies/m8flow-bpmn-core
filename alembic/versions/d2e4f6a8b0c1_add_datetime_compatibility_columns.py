"""add UTC datetime compatibility columns

Revision ID: d2e4f6a8b0c1
Revises: f1a2b3c4d5e6
Create Date: 2026-09-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d2e4f6a8b0c1"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


TIMESTAMP_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "user": (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "m8flow_tenant": (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "bpmn_process_definition": (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "bpmn_process": (
        ("start_in_seconds", "started_at"),
        ("end_in_seconds", "ended_at"),
    ),
    "task_definition": (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "task": (
        ("start_in_seconds", "started_at"),
        ("end_in_seconds", "ended_at"),
    ),
    "process_instance": (
        ("start_in_seconds", "started_at"),
        ("end_in_seconds", "ended_at"),
        ("task_updated_at_in_seconds", "task_updated_at"),
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "human_task": (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "future_task": (
        ("run_at_in_seconds", "run_at"),
        ("queued_to_run_at_in_seconds", "queued_to_run_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "process_instance_event": (("timestamp", "occurred_at"),),
    "process_instance_metadata": (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
    "process_model_bpmn_version": (("created_at_in_seconds", "created_at"),),
    "scheduler_job": (
        ("locked_at_in_seconds", "locked_at"),
        ("run_at_in_seconds", "run_at"),
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    ),
}


def upgrade() -> None:
    for table_name, pairs in TIMESTAMP_COLUMNS.items():
        for _legacy_name, native_name in pairs:
            op.add_column(
                table_name,
                sa.Column(native_name, sa.DateTime(timezone=True), nullable=True),
            )

    bind = op.get_bind()
    dialect = bind.dialect.name
    for table_name, pairs in TIMESTAMP_COLUMNS.items():
        for legacy_name, native_name in pairs:
            if dialect == "sqlite":
                expression = (
                    f"datetime({legacy_name}, 'unixepoch')"
                )
            elif dialect == "postgresql":
                expression = f"to_timestamp({legacy_name})"
            elif dialect == "mysql":
                expression = f"FROM_UNIXTIME({legacy_name})"
            else:
                continue
            op.execute(
                sa.text(
                    f"UPDATE {table_name} SET {native_name} = {expression} "
                    f"WHERE {legacy_name} IS NOT NULL"
                )
            )


def downgrade() -> None:
    for table_name, pairs in reversed(tuple(TIMESTAMP_COLUMNS.items())):
        for _legacy_name, native_name in reversed(pairs):
            op.drop_column(table_name, native_name)
