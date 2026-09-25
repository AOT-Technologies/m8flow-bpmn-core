"""Rename the principal subject constraint to the M8F namespace."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None

OLD_NAME = "principal_exactly_one_subject"
NEW_NAME = "m8f_principal_exactly_one_subject"
CHECK_SQL = (
    "(user_id IS NOT NULL AND group_id IS NULL) OR "
    "(user_id IS NULL AND group_id IS NOT NULL)"
)


def _check_constraint_names() -> set[str | None]:
    return {
        constraint.get("name")
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(
            "principal"
        )
    }


def upgrade() -> None:
    names = _check_constraint_names()
    if OLD_NAME not in names or NEW_NAME in names:
        return

    with op.batch_alter_table("principal", recreate="always") as batch_op:
        batch_op.drop_constraint(OLD_NAME, type_="check")
        batch_op.create_check_constraint(NEW_NAME, CHECK_SQL)


def downgrade() -> None:
    names = _check_constraint_names()
    if NEW_NAME not in names or OLD_NAME in names:
        return

    with op.batch_alter_table("principal", recreate="always") as batch_op:
        batch_op.drop_constraint(NEW_NAME, type_="check")
        batch_op.create_check_constraint(OLD_NAME, CHECK_SQL)
