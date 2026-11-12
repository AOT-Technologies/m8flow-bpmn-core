"""Add the signal registry to the sample app migration chain.

DDL mirrors core revision l3m4n5o6p7q8 without depending on repository-only
migration files or changing the host database's Alembic version table.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261104_0003"
down_revision = "20260706_0002"
branch_labels = None
depends_on = None


def _create_table(name: str, *elements: sa.schema.SchemaItem) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(name):
        op.create_table(name, *elements)
        return

    expected = sa.Table(name, sa.MetaData(), *elements)
    columns = {column["name"]: column for column in inspector.get_columns(name)}
    for column in expected.columns:
        actual = columns.get(column.name)
        if (
            actual is None
            or actual["nullable"] != column.nullable
            or actual["type"].compile(dialect=bind.dialect)
            != column.type.compile(dialect=bind.dialect)
        ):
            raise RuntimeError(
                f"Incompatible signal schema for {name}: missing or invalid "
                f"column {column.name}"
            )

    if inspector.get_pk_constraint(name)["constrained_columns"] != list(
        expected.primary_key.columns.keys()
    ):
        raise RuntimeError(f"Incompatible signal schema for {name}: invalid primary key")

    unique_keys = {
        tuple(key["column_names"]) for key in inspector.get_unique_constraints(name)
    }
    foreign_keys = {
        (
            tuple(key["constrained_columns"]),
            key["referred_table"],
            tuple(key["referred_columns"]),
        )
        for key in inspector.get_foreign_keys(name)
    }
    checks = {check["name"] for check in inspector.get_check_constraints(name)}
    for constraint in expected.constraints:
        valid = True
        if isinstance(constraint, sa.UniqueConstraint):
            valid = tuple(constraint.columns.keys()) in unique_keys
        elif isinstance(constraint, sa.ForeignKeyConstraint):
            targets = [
                element.target_fullname.rsplit(".", 1)
                for element in constraint.elements
            ]
            valid = (
                tuple(constraint.columns.keys()),
                targets[0][0],
                tuple(column for _, column in targets),
            ) in foreign_keys
        elif isinstance(constraint, sa.CheckConstraint):
            valid = constraint.name in checks
        if not valid:
            raise RuntimeError(
                f"Incompatible signal schema for {name}: missing or invalid "
                f"constraint {constraint.name}"
            )


def _create_index(
    name: str,
    table_name: str,
    columns: list[str],
    *,
    unique: bool,
) -> None:
    bind = op.get_bind()
    indexes = {
        index["name"]: index
        for index in sa.inspect(bind).get_indexes(table_name)
    }
    existing = indexes.get(name)
    if existing is None:
        op.create_index(name, table_name, columns, unique=unique)
    elif (
        existing["column_names"] != columns
        or existing["unique"] != unique
        or existing.get("dialect_options", {}).get(f"{bind.dialect.name}_where")
        is not None
    ):
        raise RuntimeError(
            f"Incompatible signal schema for {table_name}: invalid index {name}"
        )


def upgrade() -> None:
    _create_index(
        "m8f_process_instance_tenant_id_key",
        "process_instance",
        ["m8f_tenant_id", "id"],
        unique=True,
    )
    _create_index(
        "m8f_definition_tenant_id_key",
        "bpmn_process_definition",
        ["m8f_tenant_id", "id"],
        unique=True,
    )
    _create_table(
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
    _create_table(
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
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id", "source_process_instance_id"],
            ["process_instance.m8f_tenant_id", "process_instance.id"],
            name="fk_signal_publication_m8f_tenant_id_process_instance",
        ),
        sa.UniqueConstraint(
            "m8f_tenant_id", "occurrence_id", name="uq_signal_publication_occurrence"
        ),
        sa.UniqueConstraint(
            "m8f_tenant_id", "id", name="uq_signal_publication_tenant_id"
        ),
    )
    _create_index(
        "ix_signal_publication_m8f_tenant_id",
        "signal_publication",
        ["m8f_tenant_id"],
        unique=False,
    )
    _create_table(
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
        sa.CheckConstraint(
            "(kind = 'start' AND definition_id IS NOT NULL "
            "AND process_instance_id IS NULL) OR "
            "(kind <> 'start' AND process_instance_id IS NOT NULL)",
            name="m8f_signal_subscription_references",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_subscription"),
        sa.UniqueConstraint(
            "m8f_tenant_id", "subscription_key", name="uq_signal_subscription_key"
        ),
        sa.UniqueConstraint(
            "m8f_tenant_id", "id", name="uq_signal_subscription_tenant_id"
        ),
    )
    _create_index(
        "ix_signal_subscription_m8f_tenant_id",
        "signal_subscription",
        ["m8f_tenant_id"],
        unique=False,
    )
    _create_index(
        "ix_signal_subscription_match",
        "signal_subscription",
        ["m8f_tenant_id", "signal_name", "active"],
        unique=False,
    )
    _create_index(
        "ix_signal_subscription_process_instance_id",
        "signal_subscription",
        ["process_instance_id"],
        unique=False,
    )
    _create_table(
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
    _create_index(
        "ix_signal_delivery_due",
        "signal_delivery",
        ["m8f_tenant_id", "status", "retry_at"],
        unique=False,
    )
    _create_index(
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
