"""upgrade lane assignment id to bigint

Revision ID: 3f9a2c1d7b44
Revises: b7c1f2d9a6f1
Create Date: 2026-04-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3f9a2c1d7b44"
down_revision = "b7c1f2d9a6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("human_task", schema=None) as batch_op:
        batch_op.alter_column(
            "lane_assignment_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("human_task", schema=None) as batch_op:
        batch_op.alter_column(
            "lane_assignment_id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
