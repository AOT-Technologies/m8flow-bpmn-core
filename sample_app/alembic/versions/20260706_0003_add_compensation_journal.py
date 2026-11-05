"""Add the library's compensation journal to the sample app schema."""

from alembic import op
from m8flow_core.models import Base as BpmnCoreBase

revision = "20260706_0003"
down_revision = "20260706_0002"
branch_labels = None
depends_on = None

TABLE_NAMES = ("compensation_scope", "compensation_run", "compensation_record")


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLE_NAMES:
        BpmnCoreBase.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLE_NAMES):
        BpmnCoreBase.metadata.tables[name].drop(bind=bind, checkfirst=True)
