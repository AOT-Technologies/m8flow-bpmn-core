"""Backfill the canonical breaking-release data structures.

The preceding revision validates that the data can be migrated. This revision
adds the canonical staging columns/tables and copies data into them. Removal of
the old columns and tables happens only after downstream consumers migrate.

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "h9i0j1k2l3m4"
down_revision = "g8h9i0j1k2l3"
branch_labels = None
depends_on = None

_URI_RESOURCE_PATTERNS = (
    (re.compile(r"^/(?:v1\.0/)?tasks(?:/(?:%|\*))?/?$"), "task"),
    (
        re.compile(r"^/(?:v1\.0/)?process-instances(?:/(?:%|\*))?/?$"),
        "process_instance",
    ),
    (
        re.compile(r"^/(?:v1\.0/)?process-models(?:/(?:%|\*))?/?$"),
        "process_definition",
    ),
    (re.compile(r"^/(?:v1\.0/)?tenants(?:/(?:%|\*))?/?$"), "tenant"),
    (
        re.compile(r"^/(?:v1\.0/)?tasks/(?P<resource_id>\d+)/?$"),
        "task",
    ),
    (
        re.compile(
            r"^/(?:v1\.0/)?process-instances/(?P<resource_id>\d+)/?$"
        ),
        "process_instance",
    ),
    (
        re.compile(
            r"^/(?:v1\.0/)?process-models/(?P<resource_id>\d+)/?$"
        ),
        "process_definition",
    ),
    (
        re.compile(r"^/(?:v1\.0/)?tenants/(?P<resource_id>\d+)/?$"),
        "tenant",
    ),
)


def _table_columns(bind: sa.engine.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(
    bind: sa.engine.Connection,
    table_name: str,
    column: sa.Column[object],
) -> None:
    if column.name not in _table_columns(bind, table_name):
        op.add_column(table_name, column)


def _backfill_named_identity_columns(bind: sa.engine.Connection) -> None:
    columns = _table_columns(bind, "user")
    if not columns:
        return
    for legacy_name, named_name in (
        ("tenant_specific_field_1", "realm_identifier"),
        ("tenant_specific_field_2", "external_org_id"),
        ("tenant_specific_field_3", "external_user_id"),
    ):
        if legacy_name not in columns:
            continue
        _add_column_if_missing(
            bind,
            "user",
            sa.Column(named_name, sa.String(length=255), nullable=True),
        )
        bind.execute(
            sa.text(
                f'UPDATE "user" SET "{named_name}" = "{legacy_name}" '
                f'WHERE "{named_name}" IS NULL'
            )
        )


def _backfill_workflow_engine_fields(bind: sa.engine.Connection) -> None:
    process_columns = _table_columns(bind, "process_instance")
    if "spiff_serializer_version" in process_columns:
        _add_column_if_missing(
            bind,
            "process_instance",
            sa.Column("workflow_engine_version", sa.String(length=50), nullable=True),
        )
        bind.execute(
            sa.text(
                "UPDATE process_instance SET workflow_engine_version = "
                "spiff_serializer_version WHERE workflow_engine_version IS NULL"
            )
        )

    definition_columns = _table_columns(bind, "bpmn_process_definition")
    required = {"single_process_hash", "full_process_model_hash"}
    if not required <= definition_columns:
        return
    _add_column_if_missing(
        bind,
        "bpmn_process_definition",
        sa.Column("process_xml_digest", sa.String(length=255), nullable=True),
    )
    bind.execute(
        sa.text(
            "UPDATE bpmn_process_definition SET process_xml_digest = "
            "COALESCE(full_process_model_hash, single_process_hash) "
            "WHERE process_xml_digest IS NULL"
        )
    )


def _backfill_work_item_assignments(bind: sa.engine.Connection) -> None:
    if not {"id", "m8f_tenant_id"} <= _table_columns(bind, "work_item"):
        return
    if not {"id", "human_task_id", "user_id", "m8f_tenant_id"} <= _table_columns(
        bind, "human_task_user"
    ):
        return
    if "work_item_user" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "work_item_user",
            sa.Column("work_item_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("added_by", sa.String(length=20), nullable=True),
            sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
            sa.ForeignKeyConstraint(
                ["work_item_id"],
                ["work_item.id"],
                name="m8f_work_item_user_work_item_fk",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["user.id"], name="m8f_work_item_user_user_fk"
            ),
            sa.ForeignKeyConstraint(
                ["m8f_tenant_id"],
                ["m8flow_tenant.id"],
                name="m8f_work_item_user_tenant_fk",
            ),
            sa.PrimaryKeyConstraint(
                "work_item_id",
                "user_id",
                name="m8f_work_item_user_key",
            ),
        )
        op.create_index(
            "ix_work_item_user_tenant_id",
            "work_item_user",
            ["m8f_tenant_id"],
        )

    bind.execute(
        sa.text(
            "INSERT INTO work_item_user "
            "(work_item_id, user_id, added_by, m8f_tenant_id) "
            "SELECT human_task_id, user_id, added_by, m8f_tenant_id "
            "FROM human_task_user "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM work_item_user existing "
            "WHERE existing.work_item_id = human_task_user.human_task_id "
            "AND existing.user_id = human_task_user.user_id)"
        )
    )


def _backfill_event_categories(bind: sa.engine.Connection) -> None:
    columns = _table_columns(bind, "process_instance_event")
    if not {"event_type", "category"} <= columns:
        return
    bind.execute(
        sa.text(
            "UPDATE process_instance_event SET category = CASE "
            "WHEN event_type LIKE 'task_%' THEN 'task' "
            "WHEN event_type LIKE 'process_instance_%' THEN 'process' "
            "ELSE category END WHERE category IS NULL"
        )
    )


def _require_event_category(bind: sa.engine.Connection) -> None:
    if "category" not in _table_columns(bind, "process_instance_event"):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "process_instance_event", recreate="always"
        ) as batch_op:
            batch_op.alter_column("category", nullable=False)
    else:
        op.alter_column("process_instance_event", "category", nullable=False)


def _resource_pair_from_uri(uri: str) -> tuple[str, str | None] | None:
    normalized = uri.strip().rstrip("/")
    for pattern, resource_type in _URI_RESOURCE_PATTERNS:
        match = pattern.fullmatch(normalized)
        if match is not None:
            resource_id = match.groupdict().get("resource_id")
            return resource_type, resource_id
    return None


def _backfill_permission_targets(bind: sa.engine.Connection) -> None:
    columns = _table_columns(bind, "permission_target")
    if not {"id", "uri", "resource_type", "resource_id"} <= columns:
        return
    rows = bind.execute(
        sa.text(
            "SELECT id, uri FROM permission_target "
            "WHERE resource_type IS NULL AND resource_id IS NULL ORDER BY id"
        )
    ).fetchall()
    unmapped: list[object] = []
    for row_id, uri in rows:
        pair = _resource_pair_from_uri(uri)
        if pair is None:
            unmapped.append(row_id)
            continue
        bind.execute(
            sa.text(
                "UPDATE permission_target SET resource_type = :resource_type, "
                "resource_id = :resource_id WHERE id = :id"
            ),
            {
                "resource_type": pair[0],
                "resource_id": pair[1],
                "id": row_id,
            },
        )
    if unmapped:
        raise RuntimeError(
            "Cannot map permission target URIs to explicit resource pairs: "
            f"{unmapped[:20]}"
        )


def _drop_staging_permission_pair_check(bind: sa.engine.Connection) -> None:
    if not sa.inspect(bind).has_table("permission_target"):
        return
    constraint_name = "m8f_permission_target_resource_pair_check"
    existing = {
        item.get("name")
        for item in sa.inspect(bind).get_check_constraints("permission_target")
    }
    if constraint_name not in existing:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("permission_target", recreate="always") as batch_op:
            batch_op.drop_constraint(constraint_name, type_="check")
    else:
        op.drop_constraint(constraint_name, "permission_target", type_="check")


def upgrade() -> None:
    bind = op.get_bind()
    if "group" in sa.inspect(bind).get_table_names():
        op.rename_table("group", "m8f_group")
    _backfill_named_identity_columns(bind)
    _backfill_workflow_engine_fields(bind)
    _backfill_work_item_assignments(bind)
    _backfill_event_categories(bind)
    _require_event_category(bind)
    _drop_staging_permission_pair_check(bind)
    _backfill_permission_targets(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if "work_item_user" in sa.inspect(bind).get_table_names():
        op.drop_index("ix_work_item_user_tenant_id", table_name="work_item_user")
        op.drop_table("work_item_user")
    for table_name, column_name in (
        ("user", "realm_identifier"),
        ("user", "external_org_id"),
        ("user", "external_user_id"),
        ("process_instance", "workflow_engine_version"),
        ("bpmn_process_definition", "process_xml_digest"),
    ):
        if column_name in _table_columns(bind, table_name):
            op.drop_column(table_name, column_name)
    if "m8f_group" in sa.inspect(bind).get_table_names():
        op.rename_table("m8f_group", "group")
