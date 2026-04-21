"""add workflow state json to process instance"""

import sqlalchemy as sa
from alembic import op

revision = "b7c1f2d9a6f1"
down_revision = "4d8a3fbb7c1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("process_instance", schema=None) as batch_op:
        batch_op.add_column(sa.Column("workflow_state_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("process_instance", schema=None) as batch_op:
        batch_op.drop_column("workflow_state_json")
