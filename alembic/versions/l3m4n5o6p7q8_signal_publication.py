"""Durable signal publication registry and delivery receipts."""

import sqlalchemy as sa
from alembic import op

revision = "l3m4n5o6p7q8"
down_revision = "k2l3m4n5o6p7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "m8f_process_instance_tenant_id_key",
        "process_instance",
        ["m8f_tenant_id", "id"],
        unique=True,
    )
    op.create_index(
        "m8f_definition_tenant_id_key",
        "bpmn_process_definition",
        ["m8f_tenant_id", "id"],
        unique=True,
    )
    op.create_table(
        "signal_registry_state",
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name="fk_signal_registry_state_m8f_tenant_id_m8flow_tenant",
        ),
        sa.PrimaryKeyConstraint("m8f_tenant_id", name="pk_signal_registry_state"),
    )
    op.create_table(
        "signal_publication",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurrence_id", sa.String(length=255), nullable=False),
        sa.Column("signal_name", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source_process_instance_id", sa.Integer(), nullable=True),
        sa.Column("source_task_guid", sa.String(length=36), nullable=True),
        sa.Column("published_by_user_id", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name="fk_signal_publication_m8f_tenant_id_m8flow_tenant",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_publication"),
        sa.UniqueConstraint(
            "m8f_tenant_id", "occurrence_id", name="uq_signal_publication_occurrence"
        ),
        sa.UniqueConstraint(
            "m8f_tenant_id", "id", name="uq_signal_publication_tenant_id"
        ),
    )
    op.create_index(
        "ix_signal_publication_m8f_tenant_id",
        "signal_publication",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_table(
        "signal_subscription",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_key", sa.String(length=255), nullable=False),
        sa.Column("signal_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=True),
        sa.Column("definition_id", sa.Integer(), nullable=True),
        sa.Column("scope_id", sa.String(length=36), nullable=True),
        sa.Column("task_guid", sa.String(length=36), nullable=True),
        sa.Column("task_spec_name", sa.String(length=255), nullable=False),
        sa.Column("process_model_identifier", sa.String(length=255), nullable=True),
        sa.Column("repeatable", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("reserved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id", "definition_id"],
            ["bpmn_process_definition.m8f_tenant_id", "bpmn_process_definition.id"],
            name="fk_signal_subscription_m8f_tenant_id_bpmn_process_definition",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name="fk_signal_subscription_m8f_tenant_id_m8flow_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id", "process_instance_id"],
            ["process_instance.m8f_tenant_id", "process_instance.id"],
            name="fk_signal_subscription_m8f_tenant_id_process_instance",
        ),
        sa.CheckConstraint(
            "kind IN ('start', 'catch', 'boundary', 'event_subprocess')",
            name="m8f_signal_subscription_kind",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_subscription"),
        sa.UniqueConstraint(
            "m8f_tenant_id", "subscription_key", name="uq_signal_subscription_key"
        ),
        sa.UniqueConstraint(
            "m8f_tenant_id", "id", name="uq_signal_subscription_tenant_id"
        ),
    )
    op.create_index(
        "ix_signal_subscription_m8f_tenant_id",
        "signal_subscription",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_signal_subscription_match",
        "signal_subscription",
        ["m8f_tenant_id", "signal_name", "active"],
        unique=False,
    )
    op.create_index(
        "ix_signal_subscription_process_instance_id",
        "signal_subscription",
        ["process_instance_id"],
        unique=False,
    )
    op.create_table(
        "signal_delivery",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("process_instance_id", sa.Integer(), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name="fk_signal_delivery_m8f_tenant_id_m8flow_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id", "process_instance_id"],
            ["process_instance.m8f_tenant_id", "process_instance.id"],
            name="fk_signal_delivery_m8f_tenant_id_process_instance",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id", "publication_id"],
            ["signal_publication.m8f_tenant_id", "signal_publication.id"],
            name="fk_signal_delivery_m8f_tenant_id_signal_publication",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id", "subscription_id"],
            ["signal_subscription.m8f_tenant_id", "signal_subscription.id"],
            name="fk_signal_delivery_m8f_tenant_id_signal_subscription",
        ),
        sa.CheckConstraint("attempts >= 0", name="m8f_signal_delivery_attempts"),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'skipped')",
            name="m8f_signal_delivery_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_delivery"),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "publication_id",
            "subscription_id",
            name="uq_signal_delivery_m8f_tenant_id",
        ),
    )
    op.create_index(
        "ix_signal_delivery_due",
        "signal_delivery",
        ["m8f_tenant_id", "status", "retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_signal_delivery_m8f_tenant_id",
        "signal_delivery",
        ["m8f_tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("signal_delivery")
    op.drop_table("signal_subscription")
    op.drop_table("signal_publication")
    op.drop_table("signal_registry_state")
    op.drop_index("m8f_definition_tenant_id_key", table_name="bpmn_process_definition")
    op.drop_index("m8f_process_instance_tenant_id_key", table_name="process_instance")
