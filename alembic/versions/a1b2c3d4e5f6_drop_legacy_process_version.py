"""drop legacy process_version from process_instance

Revision ID: a1b2c3d4e5f6
Revises: 9d3a7f6c2b41
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "9d3a7f6c2b41"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        column["name"] == column_name
        for column in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    if not _has_column("process_instance", "process_version"):
        return

    with op.batch_alter_table("process_instance", schema=None) as batch_op:
        batch_op.drop_column("process_version")


def downgrade() -> None:
    if _has_column("process_instance", "process_version"):
        return

    op.add_column(
        "process_instance",
        sa.Column(
            "process_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    with op.batch_alter_table("process_instance", schema=None) as batch_op:
        batch_op.alter_column(
            "process_version",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=None,
        )
