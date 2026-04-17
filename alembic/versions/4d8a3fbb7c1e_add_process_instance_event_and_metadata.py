"""add process instance event and metadata tables"""

import sqlalchemy as sa
from alembic import op

revision = "4d8a3fbb7c1e"
down_revision = "250e246bc7a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "process_instance_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_guid", sa.String(length=36), nullable=True),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("timestamp", sa.Numeric(precision=17, scale=6), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_process_instance_event_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name=op.f(
                "fk_process_instance_event_process_instance_id_process_instance"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_process_instance_event_user_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_process_instance_event")),
    )
    with op.batch_alter_table("process_instance_event", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_process_instance_event_event_type"),
            ["event_type"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_event_m8f_tenant_id"),
            ["m8f_tenant_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_event_process_instance_id"),
            ["process_instance_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_event_task_guid"),
            ["task_guid"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_event_timestamp"),
            ["timestamp"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_event_user_id"),
            ["user_id"],
            unique=False,
        )

    op.create_table(
        "process_instance_metadata",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("updated_at_in_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at_in_seconds", sa.Integer(), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_process_instance_metadata_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name=op.f(
                "fk_process_instance_metadata_process_instance_id_process_instance"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_process_instance_metadata")),
        sa.UniqueConstraint(
            "process_instance_id",
            "key",
            name="process_instance_metadata_unique",
        ),
    )
    with op.batch_alter_table("process_instance_metadata", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_process_instance_metadata_key"),
            ["key"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_metadata_m8f_tenant_id"),
            ["m8f_tenant_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_metadata_process_instance_id"),
            ["process_instance_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("process_instance_metadata", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_process_instance_metadata_process_instance_id")
        )
        batch_op.drop_index(batch_op.f("ix_process_instance_metadata_m8f_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_process_instance_metadata_key"))

    op.drop_table("process_instance_metadata")

    with op.batch_alter_table("process_instance_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_process_instance_event_user_id"))
        batch_op.drop_index(batch_op.f("ix_process_instance_event_timestamp"))
        batch_op.drop_index(batch_op.f("ix_process_instance_event_task_guid"))
        batch_op.drop_index(
            batch_op.f("ix_process_instance_event_process_instance_id")
        )
        batch_op.drop_index(batch_op.f("ix_process_instance_event_m8f_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_process_instance_event_event_type"))

    op.drop_table("process_instance_event")
