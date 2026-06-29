"""add scheduler job table

Revision ID: f1a2b3c4d5e6
Revises: 9d3a7f6c2b41
Create Date: 2026-06-24 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "9d3a7f6c2b41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduler_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_key", sa.String(length=255), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=True),
        sa.Column("bpmn_process_definition_id", sa.Integer(), nullable=True),
        sa.Column("locked_by", sa.String(length=255), nullable=True),
        sa.Column("locked_at_in_seconds", sa.Integer(), nullable=True),
        sa.Column("run_at_in_seconds", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("updated_at_in_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at_in_seconds", sa.Integer(), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["bpmn_process_definition_id"],
            ["bpmn_process_definition.id"],
            name=op.f(
                "fk_scheduler_job_bpmn_process_definition_id_bpmn_process_definition"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_scheduler_job_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name=op.f("fk_scheduler_job_process_instance_id_process_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduler_job")),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "job_key",
            name="uq_scheduler_job_tenant_job_key",
        ),
    )
    with op.batch_alter_table("scheduler_job", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scheduler_job_bpmn_process_definition_id"),
            ["bpmn_process_definition_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_scheduler_job_job_type"),
            ["job_type"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_scheduler_job_locked_at_in_seconds"),
            ["locked_at_in_seconds"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_scheduler_job_locked_by"),
            ["locked_by"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_scheduler_job_m8f_tenant_id"),
            ["m8f_tenant_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_scheduler_job_process_instance_id"),
            ["process_instance_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_scheduler_job_run_at_in_seconds"),
            ["run_at_in_seconds"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("scheduler_job", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scheduler_job_run_at_in_seconds"))
        batch_op.drop_index(batch_op.f("ix_scheduler_job_process_instance_id"))
        batch_op.drop_index(batch_op.f("ix_scheduler_job_m8f_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_scheduler_job_locked_by"))
        batch_op.drop_index(batch_op.f("ix_scheduler_job_locked_at_in_seconds"))
        batch_op.drop_index(batch_op.f("ix_scheduler_job_job_type"))
        batch_op.drop_index(
            batch_op.f("ix_scheduler_job_bpmn_process_definition_id")
        )

    op.drop_table("scheduler_job")
