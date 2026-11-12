"""Add durable compensation journal.

Revision ID: m8f_671_compensation
Revises: l3m4n5o6p7q8
"""

import sqlalchemy as sa
from alembic import op

revision = "m8f_671_compensation"
down_revision = "l3m4n5o6p7q8"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column | sa.ForeignKeyConstraint]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "m8f_tenant_id",
            sa.String(255),
            sa.ForeignKey("m8flow_tenant.id"),
            nullable=False,
        ),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id", "process_instance_id"],
            ["process_instance.m8f_tenant_id", "process_instance.id"],
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "compensation_scope",
        *_common(),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("parent_scope_id", sa.String(36)),
        sa.Column("activity_id", sa.String(255), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("data_hash", sa.String(255), nullable=False),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "scope_id",
            name="m8f_compensation_scope_occurrence",
        ),
    )
    op.create_table(
        "compensation_run",
        *_common(),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("throw_task_guid", sa.String(36), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("wait_for_completion", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "throw_task_guid",
            name="m8f_compensation_run_occurrence",
        ),
    )
    op.create_table(
        "compensation_record",
        *_common(),
        sa.Column("source_task_guid", sa.String(36), nullable=False),
        sa.Column("activity_id", sa.String(255), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("completion_sequence", sa.Integer(), nullable=False),
        sa.Column("handler_id", sa.String(255)),
        sa.Column("handler_task_guid", sa.String(36)),
        sa.Column("run_id", sa.String(36)),
        sa.Column("throw_task_guid", sa.String(36)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failure_details", sa.Text()),
        sa.Column("data_hash", sa.String(255), nullable=False),
        sa.Column("workflow_state_hash", sa.String(255)),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "source_task_guid",
            name="m8f_compensation_record_occurrence",
        ),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "process_instance_id",
            "completion_sequence",
            name="m8f_compensation_record_sequence",
        ),
    )
    for table in ("compensation_scope", "compensation_run", "compensation_record"):
        for column in ("m8f_tenant_id", "process_instance_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("compensation_record", "compensation_run", "compensation_scope"):
        op.drop_table(table)
