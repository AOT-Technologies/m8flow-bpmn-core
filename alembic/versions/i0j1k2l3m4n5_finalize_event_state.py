"""Enforce the canonical process-instance event category domain.

Revision ID: i0j1k2l3m4n5
Revises: f7a8b9c0d1e2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i0j1k2l3m4n5"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "m8f_process_instance_event_category_check"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("process_instance_event"):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "process_instance_event", recreate="always"
        ) as batch_op:
            batch_op.create_check_constraint(
                CONSTRAINT_NAME,
                "category IN ('process', 'task')",
            )
    else:
        op.create_check_constraint(
            CONSTRAINT_NAME,
            "process_instance_event",
            "category IN ('process', 'task')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("process_instance_event"):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "process_instance_event", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
    else:
        op.drop_constraint(
            CONSTRAINT_NAME,
            "process_instance_event",
            type_="check",
        )
