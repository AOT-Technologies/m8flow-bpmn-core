"""add secret table"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260706_0002"
down_revision = "20260706_0001"
branch_labels = None
depends_on = None

REQUIRED_SECRET_COLUMNS = {
    "id",
    "m8f_tenant_id",
    "key",
    "value",
    "user_id",
    "updated_at_in_seconds",
    "created_at_in_seconds",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("secret"):
        existing_columns = {
            column["name"] for column in inspector.get_columns("secret")
        }
        missing_columns = REQUIRED_SECRET_COLUMNS - existing_columns
        if missing_columns:
            missing_columns_text = ", ".join(sorted(missing_columns))
            raise RuntimeError(
                "Existing secret table is incompatible with the sample app "
                f"migration; missing column(s): {missing_columns_text}."
            )
        return

    op.create_table(
        "secret",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("updated_at_in_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at_in_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name="fk_secret_m8f_tenant_id_m8flow_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_secret_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_secret"),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "key",
            name="secret_key_tenant_unique",
        ),
    )
    op.create_index(op.f("ix_secret_m8f_tenant_id"), "secret", ["m8f_tenant_id"])
    op.create_index(op.f("ix_secret_key"), "secret", ["key"])
    op.create_index(op.f("ix_secret_user_id"), "secret", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("secret"):
        return
    op.drop_index(op.f("ix_secret_user_id"), table_name="secret")
    op.drop_index(op.f("ix_secret_key"), table_name="secret")
    op.drop_index(op.f("ix_secret_m8f_tenant_id"), table_name="secret")
    op.drop_table("secret")
