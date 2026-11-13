"""Merge the collaboration and compensation migration heads."""

revision = "m8f_merge_runtime"
down_revision = ("m8f_collaboration_01", "m8f_671_compensation")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
