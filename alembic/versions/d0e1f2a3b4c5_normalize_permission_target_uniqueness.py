"""Make nullable permission commands participate in uniqueness checks."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

URI_INDEX = "m8f_permission_target_uri_command_identity_key"
RESOURCE_INDEX = "m8f_permission_target_resource_command_identity_key"


def _duplicate_values(query: str) -> list[tuple[object, ...]]:
    rows = op.get_bind().execute(sa.text(query)).all()
    return [tuple(row) for row in rows]


def upgrade() -> None:
    uri_duplicates = _duplicate_values(
        "SELECT uri, COALESCE(command, '') AS command_key "
        "FROM permission_target "
        "GROUP BY uri, COALESCE(command, '') "
        "HAVING COUNT(*) > 1"
    )
    if uri_duplicates:
        raise RuntimeError(
            "Cannot enforce unique permission target URI identities; duplicates: "
            f"{uri_duplicates}"
        )

    resource_duplicates = _duplicate_values(
        "SELECT resource_type, resource_id, COALESCE(command, '') AS command_key "
        "FROM permission_target "
        "WHERE resource_type IS NOT NULL AND resource_id IS NOT NULL "
        "GROUP BY resource_type, resource_id, COALESCE(command, '') "
        "HAVING COUNT(*) > 1"
    )
    if resource_duplicates:
        raise RuntimeError(
            "Cannot enforce unique permission resource identities; duplicates: "
            f"{resource_duplicates}"
        )

    existing_indexes = {
        index.get("name") for index in sa.inspect(op.get_bind()).get_indexes(
            "permission_target"
        )
    }
    if URI_INDEX not in existing_indexes:
        op.create_index(
            URI_INDEX,
            "permission_target",
            ["uri", sa.text("COALESCE(command, '')")],
            unique=True,
        )
    if RESOURCE_INDEX not in existing_indexes:
        op.create_index(
            RESOURCE_INDEX,
            "permission_target",
            [
                "resource_type",
                "resource_id",
                sa.text("COALESCE(command, '')"),
            ],
            unique=True,
            postgresql_where=sa.text(
                "resource_type IS NOT NULL AND resource_id IS NOT NULL"
            ),
            sqlite_where=sa.text(
                "resource_type IS NOT NULL AND resource_id IS NOT NULL"
            ),
        )


def downgrade() -> None:
    existing_indexes = {
        index.get("name")
        for index in sa.inspect(op.get_bind()).get_indexes("permission_target")
    }
    if RESOURCE_INDEX in existing_indexes:
        op.drop_index(RESOURCE_INDEX, table_name="permission_target")
    if URI_INDEX in existing_indexes:
        op.drop_index(URI_INDEX, table_name="permission_target")
