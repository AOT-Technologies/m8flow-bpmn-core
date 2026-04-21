"""add definition source xml"""

import sqlalchemy as sa
from alembic import op

revision = "1e7c8d4a5b9f"
down_revision = "b7c1f2d9a6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bpmn_process_definition",
        sa.Column("source_bpmn_xml", sa.Text(), nullable=True),
    )
    op.add_column(
        "bpmn_process_definition",
        sa.Column("source_dmn_xml", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bpmn_process_definition", "source_dmn_xml")
    op.drop_column("bpmn_process_definition", "source_bpmn_xml")
