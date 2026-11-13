"""Add instance correlation keys for BPMN collaborations."""

import sqlalchemy as sa
from alembic import op

revision = "m8f_collaboration_01"
down_revision = "k2l3m4n5o6p7"
branch_labels = None
depends_on = None

_INDEX = "ix_process_instance_collaboration_lookup"


def upgrade() -> None:
    op.add_column(
        "process_instance",
        sa.Column("collaboration_correlation_key", sa.String(255), nullable=True),
    )
    op.create_index(
        _INDEX,
        "process_instance",
        [
            "m8f_tenant_id",
            "bpmn_process_definition_id",
            "bpmn_version_id",
            "collaboration_correlation_key",
        ],
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="process_instance")
    op.drop_column("process_instance", "collaboration_correlation_key")
