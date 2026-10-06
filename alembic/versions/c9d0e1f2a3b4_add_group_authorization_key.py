"""Add a nullable identity key for race-safe authorization group creation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "m8f_group_authorization_key"


def upgrade() -> None:
    op.add_column(
        "group",
        sa.Column("authorization_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_group_authorization_key",
        "group",
        ["authorization_key"],
        unique=False,
    )
    with op.batch_alter_table("group", recreate="always") as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT_NAME, ["authorization_key"])


def downgrade() -> None:
    with op.batch_alter_table("group", recreate="always") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")
    op.drop_index("ix_group_authorization_key", table_name="group")
    op.drop_column("group", "authorization_key")
