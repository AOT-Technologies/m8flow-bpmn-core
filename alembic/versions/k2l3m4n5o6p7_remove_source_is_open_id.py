"""Remove the legacy OpenID source flag from groups.

The group source flag was an identity-provider implementation detail rather
than workflow authorization state.  The breaking schema now keeps group
identity in ``identifier`` and ``authorization_key`` only.

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "k2l3m4n5o6p7"
down_revision = "j1k2l3m4n5o6"
branch_labels = None
depends_on = None

TABLE_NAME = "m8f_group"
COLUMN_NAME = "source_is_open_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    if COLUMN_NAME not in columns:
        return

    if bind.dialect.name == "sqlite":
        for index in inspector.get_indexes(TABLE_NAME):
            if COLUMN_NAME in index.get("column_names", ()):
                op.drop_index(index["name"], table_name=TABLE_NAME)
        with op.batch_alter_table(TABLE_NAME, recreate="always") as batch_op:
            batch_op.drop_column(COLUMN_NAME)
    else:
        op.drop_column(TABLE_NAME, COLUMN_NAME)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    if COLUMN_NAME in columns:
        return

    column = sa.Column(
        COLUMN_NAME,
        sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE_NAME, recreate="always") as batch_op:
            batch_op.add_column(column)
    else:
        op.add_column(TABLE_NAME, column)
