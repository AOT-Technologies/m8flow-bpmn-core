"""add user service identity fields"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8c52f4d1a7e3"
down_revision = ("1e7c8d4a5b9f", "3f9a2c1d7b44")
branch_labels = None
depends_on = None

DEFAULT_SERVICE_URL = "http://localhost:7002/realms/local"


def upgrade() -> None:
    op.add_column("user", sa.Column("service", sa.String(length=255), nullable=True))
    op.add_column(
        "user", sa.Column("service_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "user", sa.Column("display_name", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "user",
        sa.Column("updated_at_in_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("created_at_in_seconds", sa.Integer(), nullable=True),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            'UPDATE "user" '
            "SET service = :service, "
            "service_id = username, "
            "display_name = username, "
            "created_at_in_seconds = COALESCE(created_at_in_seconds, 0), "
            "updated_at_in_seconds = COALESCE(updated_at_in_seconds, 0)"
        ),
        {"service": DEFAULT_SERVICE_URL},
    )

    op.alter_column("user", "service", nullable=False)
    op.alter_column("user", "service_id", nullable=False)

    op.drop_index(op.f("ix_user_username"), table_name="user")
    op.create_index(op.f("ix_user_username"), "user", ["username"], unique=False)
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=False)
    op.create_index(op.f("ix_user_service"), "user", ["service"], unique=False)
    op.create_index(
        op.f("ix_user_service_id"), "user", ["service_id"], unique=False
    )
    op.create_unique_constraint("service_key", "user", ["service", "service_id"])


def downgrade() -> None:
    op.drop_constraint("service_key", "user", type_="unique")
    op.drop_index(op.f("ix_user_service_id"), table_name="user")
    op.drop_index(op.f("ix_user_service"), table_name="user")
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_index(op.f("ix_user_username"), table_name="user")

    op.drop_column("user", "created_at_in_seconds")
    op.drop_column("user", "updated_at_in_seconds")
    op.drop_column("user", "display_name")
    op.drop_column("user", "service_id")
    op.drop_column("user", "service")

    op.create_index(op.f("ix_user_username"), "user", ["username"], unique=True)
