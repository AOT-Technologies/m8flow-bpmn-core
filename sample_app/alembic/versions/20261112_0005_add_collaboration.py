"""Add collaboration support to an existing sample app schema."""

import sqlalchemy as sa
from alembic import op

revision = "20261112_0005"
down_revision = "20261110_0004"
branch_labels = None
depends_on = None

_TABLE = "process_instance"
_COLUMN = "collaboration_correlation_key"
_INDEX = "ix_process_instance_collaboration_lookup"
_FK = "fk_process_instance_definition_tenant"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    foreign_keys = {key["name"] for key in inspector.get_foreign_keys(_TABLE)}
    if _FK not in foreign_keys:
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

    # Fresh databases can already have these objects through the ORM baseline.
    if _COLUMN not in {column["name"] for column in inspector.get_columns(_TABLE)}:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(255), nullable=True))
    if _INDEX not in {index["name"] for index in inspector.get_indexes(_TABLE)}:
        op.create_index(
            _INDEX,
            _TABLE,
            ["m8f_tenant_id", "bpmn_process_definition_id", "bpmn_version_id", _COLUMN],
        )
    if _FK not in foreign_keys:
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_foreign_key(
                _FK,
                "bpmn_process_definition",
                ["m8f_tenant_id", "bpmn_process_definition_id"],
                ["m8f_tenant_id", "id"],
            )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_FK, type_="foreignkey")
        batch.drop_column(_COLUMN)
