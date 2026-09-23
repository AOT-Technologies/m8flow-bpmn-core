"""Add explicit permission resource target fields.

URI targets remain available for compatibility. New callers can match an exact
resource type/id pair without relying on URI wildcard parsing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "permission_target",
        sa.Column("resource_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "permission_target",
        sa.Column("resource_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_permission_target_resource_type",
        "permission_target",
        ["resource_type"],
        unique=False,
    )
    op.create_index(
        "ix_permission_target_resource_id",
        "permission_target",
        ["resource_id"],
        unique=False,
    )
    with op.batch_alter_table("permission_target", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            "m8f_permission_target_resource_command_key",
            ["resource_type", "resource_id", "command"],
        )


def downgrade() -> None:
    with op.batch_alter_table("permission_target", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "m8f_permission_target_resource_command_key", type_="unique"
        )
    op.drop_index(
        "ix_permission_target_resource_id", table_name="permission_target"
    )
    op.drop_index(
        "ix_permission_target_resource_type", table_name="permission_target"
    )
    op.drop_column("permission_target", "resource_id")
    op.drop_column("permission_target", "resource_type")
