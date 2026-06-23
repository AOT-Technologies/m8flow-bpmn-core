"""add rbac permission schema

Revision ID: 9d3a7f6c2b41
Revises: c4d0f9b1e2aa
Create Date: 2026-06-19 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9d3a7f6c2b41"
down_revision = "c4d0f9b1e2aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "permission_target",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uri", sa.String(length=255), nullable=False),
        sa.Column("command", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_target")),
        sa.UniqueConstraint(
            "uri",
            "command",
            name="permission_target_uri_command_unique",
        ),
    )
    with op.batch_alter_table("permission_target", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_permission_target_uri"),
            ["uri"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_permission_target_command"),
            ["command"],
            unique=False,
        )

    op.create_table(
        "user_group_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["group.id"],
            name=op.f("fk_user_group_assignment_group_id_group"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_user_group_assignment_user_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_group_assignment")),
        sa.UniqueConstraint(
            "user_id",
            "group_id",
            name="user_group_assignment_unique",
        ),
    )
    with op.batch_alter_table("user_group_assignment", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_group_assignment_group_id"),
            ["group_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_user_group_assignment_user_id"),
            ["user_id"],
            unique=False,
        )

    op.create_table(
        "principal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            (
                "(user_id IS NOT NULL AND group_id IS NULL) OR "
                "(user_id IS NULL AND group_id IS NOT NULL)"
            ),
            name="principal_exactly_one_subject",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["group.id"],
            name=op.f("fk_principal_group_id_group"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_principal_user_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_principal")),
        sa.UniqueConstraint("group_id"),
        sa.UniqueConstraint("user_id"),
    )
    with op.batch_alter_table("principal", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_principal_group_id"),
            ["group_id"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_principal_user_id"),
            ["user_id"],
            unique=True,
        )

    op.create_table(
        "permission_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("principal_id", sa.Integer(), nullable=False),
        sa.Column("permission_target_id", sa.Integer(), nullable=False),
        sa.Column("grant_type", sa.String(length=50), nullable=False),
        sa.Column("permission", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_target_id"],
            ["permission_target.id"],
            name=op.f(
                "fk_permission_assignment_permission_target_id_permission_target"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principal.id"],
            name=op.f("fk_permission_assignment_principal_id_principal"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_assignment")),
        sa.UniqueConstraint(
            "principal_id",
            "permission_target_id",
            "permission",
            name="permission_assignment_unique",
        ),
    )
    with op.batch_alter_table("permission_assignment", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_permission_assignment_permission_target_id"),
            ["permission_target_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_permission_assignment_principal_id"),
            ["principal_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("permission_assignment", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_permission_assignment_principal_id"))
        batch_op.drop_index(
            batch_op.f("ix_permission_assignment_permission_target_id")
        )
    op.drop_table("permission_assignment")

    with op.batch_alter_table("principal", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_principal_user_id"))
        batch_op.drop_index(batch_op.f("ix_principal_group_id"))
    op.drop_table("principal")

    with op.batch_alter_table("user_group_assignment", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_group_assignment_user_id"))
        batch_op.drop_index(batch_op.f("ix_user_group_assignment_group_id"))
    op.drop_table("user_group_assignment")

    with op.batch_alter_table("permission_target", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_permission_target_command"))
        batch_op.drop_index(batch_op.f("ix_permission_target_uri"))
    op.drop_table("permission_target")
