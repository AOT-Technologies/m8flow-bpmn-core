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
                "SELECT DISTINCT m8f_tenant_id, json_data_hash "
                "FROM bpmn_process"
            )
        )
    )
    assigned: set[str] = set()
    for tenant_id, payload_hash in references:
        if payload_hash not in json_rows:
            continue
        if payload_hash not in assigned:
            bind.execute(
                sa.text(
                    "UPDATE json_data SET m8f_tenant_id = :tenant_id "
                    "WHERE hash = :payload_hash"
                ),
                {"tenant_id": tenant_id, "payload_hash": payload_hash},
            )
            assigned.add(payload_hash)
            continue
        bind.execute(
            sa.text(
                "INSERT INTO json_data (m8f_tenant_id, hash, data) "
                "VALUES (:tenant_id, :payload_hash, :data)"
            ).bindparams(sa.bindparam("data", type_=sa.JSON)),
            {
                "tenant_id": tenant_id,
                "payload_hash": payload_hash,
                "data": json_rows[payload_hash],
            },
        )

    # Unreferenced content cannot be safely assigned to a tenant and is not
    # part of the runtime graph, so remove it before making the composite key.
    bind.execute(
        sa.text(
            "DELETE FROM json_data WHERE m8f_tenant_id IS NULL"
        )
    )
    with op.batch_alter_table("json_data", recreate="always") as batch_op:
        batch_op.drop_constraint("pk_json_data", type_="primary")
        batch_op.create_foreign_key(
            "m8f_json_data_tenant_fk",
            "m8flow_tenant",
            ["m8f_tenant_id"],
            ["id"],
        )
        batch_op.create_primary_key(
            "m8f_json_data_tenant_hash_pk", ["m8f_tenant_id", "hash"]
        )
        batch_op.alter_column("m8f_tenant_id", nullable=False)


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
    # Downgrade intentionally restores the old shape; data duplicated for a
    # second tenant is retained only in the first tenant's row.
    with op.batch_alter_table("json_data", recreate="always") as batch_op:
        batch_op.drop_constraint("m8f_json_data_tenant_fk", type_="foreignkey")
        batch_op.drop_constraint("m8f_json_data_tenant_hash_pk", type_="primary")
        batch_op.create_primary_key("pk_json_data", ["hash"])
        batch_op.drop_column("m8f_tenant_id")
    op.drop_index(
        "ix_process_instance_event_category", table_name="process_instance_event"
    )
    op.drop_column("process_instance_event", "category")
