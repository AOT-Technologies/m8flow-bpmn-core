"""Require explicit permission targets to contain complete resource pairs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "m8f_permission_target_resource_pair_check"
CHECK_SQL = (
    "(resource_type IS NULL AND resource_id IS NULL) OR "
    "(resource_type IS NOT NULL AND resource_id IS NOT NULL)"
)


def upgrade() -> None:
    bind = op.get_bind()
    invalid_rows = bind.execute(
        sa.text(
            "SELECT id FROM permission_target "
            "WHERE (resource_type IS NULL) <> (resource_id IS NULL) "
            "ORDER BY id"
        )
    ).scalars().all()
    if invalid_rows:
        raise RuntimeError(
            "Cannot enforce complete permission resource pairs; invalid "
            f"permission_target rows: {invalid_rows}"
        )

    existing_names = {
        constraint.get("name")
        for constraint in sa.inspect(bind).get_check_constraints(
            "permission_target"
        )
    }
    if CONSTRAINT_NAME in existing_names:
        return

    with op.batch_alter_table("permission_target", recreate="always") as batch_op:
        batch_op.create_check_constraint(CONSTRAINT_NAME, CHECK_SQL)


def downgrade() -> None:
    existing_names = {
        constraint.get("name")
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(
            "permission_target"
        )
    }
    if CONSTRAINT_NAME not in existing_names:
        return

    with op.batch_alter_table("permission_target", recreate="always") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
