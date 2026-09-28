"""add canonical work-item claim state table

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Some host-owned migration tests may carry only a subset of the library
    # schema. There is no legacy human-task state to backfill in that case.
    if "human_task" not in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "work_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.Column("task_guid", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=50), nullable=True),
        sa.Column("lane_assignment_id", sa.Integer(), nullable=True),
        sa.Column("completed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("actual_owner_id", sa.Integer(), nullable=True),
        sa.Column("task_status", sa.String(length=50), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("updated_at_in_seconds", sa.BigInteger(), nullable=True),
        sa.Column("created_at_in_seconds", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["id"],
            ["human_task.id"],
            name="m8f_work_item_human_task_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"], ["m8flow_tenant.id"], name="m8f_work_item_tenant_fk"
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name="m8f_work_item_process_instance_fk",
        ),
        sa.ForeignKeyConstraint(
            ["task_guid"], ["task.guid"], name="m8f_work_item_task_fk"
        ),
        sa.ForeignKeyConstraint(
            ["lane_assignment_id"], ["group.id"], name="m8f_work_item_lane_group_fk"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_user_id"],
            ["user.id"],
            name="m8f_work_item_completed_by_user_fk",
        ),
        sa.ForeignKeyConstraint(
            ["actual_owner_id"], ["user.id"], name="m8f_work_item_actual_owner_fk"
        ),
        sa.PrimaryKeyConstraint("id", name="m8f_work_item_pk"),
    )
    with op.batch_alter_table("work_item") as batch_op:
        batch_op.create_index(
            "ix_work_item_m8f_tenant_id", ["m8f_tenant_id"], unique=False
        )
        batch_op.create_index(
            "ix_work_item_process_instance_id", ["process_instance_id"], unique=False
        )
        batch_op.create_index("ix_work_item_task_guid", ["task_guid"], unique=False)
        batch_op.create_index(
            "ix_work_item_lane_assignment_id", ["lane_assignment_id"], unique=False
        )
        batch_op.create_index(
            "ix_work_item_completed_by_user_id",
            ["completed_by_user_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_work_item_actual_owner_id", ["actual_owner_id"], unique=False
        )
        batch_op.create_index("ix_work_item_completed", ["completed"], unique=False)

    op.execute(
        sa.text(
            "INSERT INTO work_item ("
            "id, process_instance_id, task_guid, task_id, lane_assignment_id, "
            "completed_by_user_id, actual_owner_id, task_status, completed, "
            "updated_at_in_seconds, created_at_in_seconds, updated_at, created_at, "
            "m8f_tenant_id) "
            "SELECT id, process_instance_id, task_guid, task_id, lane_assignment_id, "
            "completed_by_user_id, actual_owner_id, task_status, completed, "
            "updated_at_in_seconds, created_at_in_seconds, updated_at, created_at, "
            "m8f_tenant_id FROM human_task"
        )
    )


def downgrade() -> None:
    op.drop_table("work_item")
