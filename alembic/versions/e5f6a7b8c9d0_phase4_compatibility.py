"""Add tenant-scoped JSON data, event categories, and M8F constraint names.

The model/table names and legacy columns remain unchanged.  This migration
only adds the tenant discriminator/category and replaces constraint names.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d2e4f6a8b0c1"
branch_labels = None
depends_on = None


CONSTRAINT_RENAMES = (
    ("user", "service_key", "m8f_user_service_id_key", "unique"),
    (
        "bpmn_process_definition",
        "bpmn_process_definition_full_process_model_hash_tenant_unique",
        "m8f_bpmn_process_definition_full_process_model_hash_tenant_key",
        "unique",
    ),
    (
        "bpmn_process_definition",
        "process_hash_unique",
        "m8f_bpmn_process_definition_process_hash_key",
        "unique",
    ),
    (
        "permission_target",
        "permission_target_uri_command_unique",
        "m8f_permission_target_uri_command_key",
        "unique",
    ),
    (
        "permission_assignment",
        "permission_assignment_unique",
        "m8f_permission_assignment_principal_target_permission_key",
        "unique",
    ),
    (
        "user_group_assignment",
        "user_group_assignment_unique",
        "m8f_user_group_assignment_user_group_key",
        "unique",
    ),
    (
        "task_definition",
        "task_definition_unique",
        "m8f_task_definition_tenant_process_key",
        "unique",
    ),
    (
        "human_task_user",
        "human_task_user_unique",
        "m8f_human_task_user_key",
        "unique",
    ),
    (
        "process_instance_metadata",
        "process_instance_metadata_unique",
        "m8f_process_instance_metadata_key",
        "unique",
    ),
    (
        "future_task",
        "future_task_task_guid_fk",
        "m8f_future_task_task_guid_fk",
        "foreignkey",
    ),
)


def _rename_constraints() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, old_name, new_name, kind in CONSTRAINT_RENAMES:
        if kind == "unique":
            names = {
                item.get("name")
                for item in inspector.get_unique_constraints(table_name)
            }
        else:
            names = {
                item.get("name") for item in inspector.get_foreign_keys(table_name)
            }
        if old_name not in names:
            continue
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            batch_op.drop_constraint(old_name, type_=kind)
            if kind == "unique":
                columns = next(
                    item["column_names"]
                    for item in inspector.get_unique_constraints(table_name)
                    if item.get("name") == old_name
                )
                batch_op.create_unique_constraint(new_name, columns)
            else:
                foreign_key = next(
                    item
                    for item in inspector.get_foreign_keys(table_name)
                    if item.get("name") == old_name
                )
                batch_op.create_foreign_key(
                    new_name,
                    foreign_key["referred_table"],
                    foreign_key["constrained_columns"],
                    foreign_key["referred_columns"],
                    ondelete=(foreign_key.get("options") or {}).get("ondelete"),
                )
        inspector = sa.inspect(bind)


def _tenant_scope_json_data() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "ALTER TABLE json_data ADD COLUMN m8f_tenant_id VARCHAR(255)"
        )
    )

    json_rows = {
        row.hash: row.data
        for row in bind.execute(sa.text("SELECT hash, data FROM json_data"))
    }
    references = list(
        bind.execute(
            sa.text(
                "SELECT m8f_tenant_id, json_data_hash AS payload_hash "
                "FROM bpmn_process "
                "UNION ALL "
                "SELECT m8f_tenant_id, json_data_hash AS payload_hash "
                "FROM task "
                "UNION ALL "
                "SELECT m8f_tenant_id, python_env_data_hash AS payload_hash "
                "FROM task"
            )
        )
    )

    reference_tenants: dict[str, set[str]] = {}
    invalid_references: list[str] = []
    for tenant_id, payload_hash in references:
        if tenant_id is None or payload_hash is None:
            invalid_references.append(
                f"tenant={tenant_id!r}, hash={payload_hash!r}"
            )
            continue
        reference_tenants.setdefault(payload_hash, set()).add(tenant_id)

    missing_payloads = sorted(set(reference_tenants) - set(json_rows))
    unreferenced_payloads = sorted(set(json_rows) - set(reference_tenants))
    referenced_tenants = {
        tenant_id
        for tenant_ids in reference_tenants.values()
        for tenant_id in tenant_ids
    }
    known_tenants = {
        row.id
        for row in bind.execute(sa.text("SELECT id FROM m8flow_tenant"))
    }
    missing_tenants = sorted(referenced_tenants - known_tenants)

    validation_errors: list[str] = []
    if invalid_references:
        validation_errors.append(
            "null tenant or payload reference: "
            + ", ".join(invalid_references[:5])
        )
    if missing_payloads:
        validation_errors.append(
            "referenced payload hashes are missing from json_data: "
            + ", ".join(missing_payloads[:5])
        )
    if unreferenced_payloads:
        validation_errors.append(
            "json_data rows have no tenant-qualified reference: "
            + ", ".join(unreferenced_payloads[:5])
        )
    if missing_tenants:
        validation_errors.append(
            "referenced tenants are missing from m8flow_tenant: "
            + ", ".join(missing_tenants[:5])
        )
    if validation_errors:
        raise RuntimeError(
            "Cannot safely tenant-scope json_data; no payload rows were "
            "re-keyed. "
            + " | ".join(validation_errors)
        )

    # A shared legacy hash is valid: it represents identical content referenced
    # by multiple tenants. Build the complete target shape separately because
    # inserting a second copy into the legacy hash-only table would violate its
    # primary key before the schema replacement occurs.
    stage_table = "m8f_json_data_tenant_scope_stage"
    op.create_table(
        stage_table,
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.Column("hash", sa.String(length=255), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name="m8f_json_data_tenant_fk",
        ),
        sa.PrimaryKeyConstraint(
            "m8f_tenant_id",
            "hash",
            name="m8f_json_data_tenant_hash_pk",
        ),
    )
    for payload_hash, tenant_ids in reference_tenants.items():
        for tenant_id in sorted(tenant_ids):
            bind.execute(
                sa.text(
                    f"INSERT INTO {stage_table} "
                    "(m8f_tenant_id, hash, data) "
                    "VALUES (:tenant_id, :payload_hash, :data)"
                ).bindparams(sa.bindparam("data", type_=sa.JSON)),
                {
                    "tenant_id": tenant_id,
                    "payload_hash": payload_hash,
                    "data": json_rows[payload_hash],
                },
            )

    op.drop_table("json_data")
    op.rename_table(stage_table, "json_data")


def upgrade() -> None:
    op.add_column(
        "process_instance_event",
        sa.Column("category", sa.String(length=20), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE process_instance_event SET category = CASE "
            "WHEN event_type LIKE 'task_%' THEN 'task' ELSE 'process' END"
        )
    )
    op.create_index(
        "ix_process_instance_event_category",
        "process_instance_event",
        ["category"],
        unique=False,
    )
    _tenant_scope_json_data()
    _rename_constraints()


def downgrade() -> None:
    # Downgrade intentionally restores the old shape. A legacy hash-only key
    # cannot retain one row per tenant, so identical hashes are collapsed to
    # the lexicographically first tenant before the old primary key returns.
    op.execute(
        sa.text(
            "DELETE FROM json_data WHERE (m8f_tenant_id, hash) NOT IN ("
            "SELECT MIN(m8f_tenant_id), hash FROM json_data GROUP BY hash"
            ")"
        )
    )
    with op.batch_alter_table("json_data", recreate="always") as batch_op:
        batch_op.drop_constraint("m8f_json_data_tenant_fk", type_="foreignkey")
        batch_op.drop_constraint("m8f_json_data_tenant_hash_pk", type_="primary")
        batch_op.create_primary_key("pk_json_data", ["hash"])
        batch_op.drop_column("m8f_tenant_id")
    op.drop_index(
        "ix_process_instance_event_category", table_name="process_instance_event"
    )
    op.drop_column("process_instance_event", "category")
