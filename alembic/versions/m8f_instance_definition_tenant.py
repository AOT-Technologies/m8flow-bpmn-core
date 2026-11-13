"""Enforce tenant ownership of an instance's BPMN definition."""

import sqlalchemy as sa
from alembic import op

revision = "m8f_instance_definition_tenant"
down_revision = "m8f_merge_runtime"
branch_labels = None
depends_on = None

_TABLE = "process_instance"
_FK = "fk_process_instance_definition_tenant"
_DEFINITION = "bpmn_process_definition"
_INSTANCE_COLUMNS = ["m8f_tenant_id", "bpmn_process_definition_id"]
_DEFINITION_COLUMNS = ["m8f_tenant_id", "id"]


def upgrade() -> None:
    bind = op.get_bind()
    if not op.get_context().as_sql:
        invalid_id = bind.scalar(
            sa.text(
                "SELECT i.id FROM process_instance AS i "
                "LEFT JOIN bpmn_process_definition AS d "
                "ON d.id = i.bpmn_process_definition_id "
                "AND d.m8f_tenant_id = i.m8f_tenant_id "
                "WHERE i.bpmn_process_definition_id IS NOT NULL AND d.id IS NULL "
                "ORDER BY i.id LIMIT 1"
            )
        )
        if invalid_id is not None:
            raise RuntimeError(
                f"Process instance {invalid_id} references a missing BPMN definition "
                "or one owned by another tenant. Correct the definition reference "
                "before retrying this migration."
            )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_foreign_key(
                _FK, _DEFINITION, _INSTANCE_COLUMNS, _DEFINITION_COLUMNS
            )
    else:
        op.create_foreign_key(
            _FK, _TABLE, _DEFINITION, _INSTANCE_COLUMNS, _DEFINITION_COLUMNS
        )


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_FK, type_="foreignkey")
