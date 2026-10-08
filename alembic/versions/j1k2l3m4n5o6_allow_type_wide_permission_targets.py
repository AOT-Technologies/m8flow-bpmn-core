"""Allow explicit type-wide permission targets.

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "j1k2l3m4n5o6"
down_revision = "i0j1k2l3m4n5"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "m8f_permission_target_resource_pair_check"
RESOURCE_TYPE_CONSTRAINT_NAME = "m8f_permission_target_resource_type_check"
RESOURCE_TYPE_CHECK = (
    "resource_type IN ('process_definition', 'process_instance', "
    "'process_model', 'task', 'tenant')"
)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("permission_target"):
        return
    if bind.dialect.name == "sqlite":
        existing = {
            item.get("name")
            for item in sa.inspect(bind).get_check_constraints("permission_target")
        }
        with op.batch_alter_table(
            "permission_target", recreate="always"
        ) as batch_op:
            if CONSTRAINT_NAME in existing:
                batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
            batch_op.create_check_constraint(
                CONSTRAINT_NAME,
                "resource_type IS NOT NULL",
            )
            batch_op.create_check_constraint(
                RESOURCE_TYPE_CONSTRAINT_NAME,
                RESOURCE_TYPE_CHECK,
            )
    else:
        existing = {
            item.get("name")
            for item in sa.inspect(bind).get_check_constraints(
                "permission_target"
            )
        }
        if CONSTRAINT_NAME in existing:
            op.drop_constraint(
                CONSTRAINT_NAME, "permission_target", type_="check"
            )
        op.create_check_constraint(
            CONSTRAINT_NAME,
            "permission_target",
            "resource_type IS NOT NULL",
        )
        op.create_check_constraint(
            RESOURCE_TYPE_CONSTRAINT_NAME,
            "permission_target",
            RESOURCE_TYPE_CHECK,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("permission_target"):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "permission_target", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
            batch_op.drop_constraint(
                RESOURCE_TYPE_CONSTRAINT_NAME, type_="check"
            )
            batch_op.create_check_constraint(
                CONSTRAINT_NAME,
                "(resource_type IS NULL AND resource_id IS NULL) OR "
                "(resource_type IS NOT NULL AND resource_id IS NOT NULL)",
            )
    else:
        op.drop_constraint(CONSTRAINT_NAME, "permission_target", type_="check")
        op.drop_constraint(
            RESOURCE_TYPE_CONSTRAINT_NAME,
            "permission_target",
            type_="check",
        )
        op.create_check_constraint(
            CONSTRAINT_NAME,
            "permission_target",
            "(resource_type IS NULL AND resource_id IS NULL) OR "
            "(resource_type IS NOT NULL AND resource_id IS NOT NULL)",
        )
