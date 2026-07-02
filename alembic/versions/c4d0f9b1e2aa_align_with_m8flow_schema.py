"""align workflow tables with m8flow schema

Revision ID: c4d0f9b1e2aa
Revises: 8c52f4d1a7e3
Create Date: 2026-06-15 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d0f9b1e2aa"
down_revision = "8c52f4d1a7e3"
branch_labels = None
depends_on = None


TENANT_STATUS_ENUM = sa.Enum(
    "ACTIVE",
    "INACTIVE",
    "DELETED",
    name="tenantstatus",
)


def upgrade() -> None:
    bind = op.get_bind()
    TENANT_STATUS_ENUM.create(bind, checkfirst=True)

    op.create_table(
        "group",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("identifier", sa.String(length=255), nullable=True),
        sa.Column(
            "source_is_open_id",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group")),
    )
    with op.batch_alter_table("group", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_group_identifier"), ["identifier"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_group_name"), ["name"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_group_source_is_open_id"),
            ["source_is_open_id"],
            unique=False,
        )

    op.create_table(
        "json_data",
        sa.Column("hash", sa.String(length=255), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("hash", name=op.f("pk_json_data")),
    )

    op.create_table(
        "process_model_bpmn_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("process_model_identifier", sa.String(length=255), nullable=False),
        sa.Column("bpmn_xml_hash", sa.String(length=64), nullable=False),
        sa.Column("bpmn_xml_file_contents", sa.Text(), nullable=False),
        sa.Column("created_at_in_seconds", sa.Integer(), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f(
                "fk_process_model_bpmn_version_m8f_tenant_id_m8flow_tenant"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_process_model_bpmn_version")),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "process_model_identifier",
            "bpmn_xml_hash",
            name="uq_process_model_bpmn_version_tenant_model_hash",
        ),
    )
    with op.batch_alter_table("process_model_bpmn_version", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_process_model_bpmn_version_bpmn_xml_hash"),
            ["bpmn_xml_hash"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_model_bpmn_version_created_at_in_seconds"),
            ["created_at_in_seconds"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_model_bpmn_version_m8f_tenant_id"),
            ["m8f_tenant_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_model_bpmn_version_process_model_identifier"),
            ["process_model_identifier"],
            unique=False,
        )

    op.add_column(
        "m8flow_tenant",
        sa.Column(
            "status",
            TENANT_STATUS_ENUM,
            nullable=True,
            server_default="ACTIVE",
        ),
    )
    op.add_column(
        "m8flow_tenant",
        sa.Column("created_at_in_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "m8flow_tenant",
        sa.Column("updated_at_in_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "m8flow_tenant",
        sa.Column("created_by", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "m8flow_tenant",
        sa.Column("modified_by", sa.String(length=255), nullable=True),
    )

    op.add_column(
        "user",
        sa.Column("tenant_specific_field_1", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("tenant_specific_field_2", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("tenant_specific_field_3", sa.String(length=255), nullable=True),
    )

    op.add_column(
        "bpmn_process",
        sa.Column("start_in_seconds", sa.Numeric(precision=17, scale=6), nullable=True),
    )
    op.add_column(
        "bpmn_process",
        sa.Column("end_in_seconds", sa.Numeric(precision=17, scale=6), nullable=True),
    )

    op.add_column(
        "process_instance",
        sa.Column("spiff_serializer_version", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "process_instance",
        sa.Column("task_updated_at_in_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "process_instance",
        sa.Column("bpmn_version_control_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "process_instance",
        sa.Column(
            "bpmn_version_control_identifier",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "process_instance",
        sa.Column("last_milestone_bpmn_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "process_instance",
        sa.Column("bpmn_version_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("process_instance", schema=None) as batch_op:
        batch_op.create_foreign_key(
            batch_op.f("fk_process_instance_bpmn_version_id_process_model_bpmn_version"),
            "process_model_bpmn_version",
            ["bpmn_version_id"],
            ["id"],
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_bpmn_version_id"),
            ["bpmn_version_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_process_instance_last_milestone_bpmn_name"),
            ["last_milestone_bpmn_name"],
            unique=False,
        )

    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.alter_column(
            "state",
            existing_type=sa.String(length=20),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "start_in_seconds",
            existing_type=sa.Integer(),
            type_=sa.Numeric(precision=17, scale=6),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "end_in_seconds",
            existing_type=sa.Integer(),
            type_=sa.Numeric(precision=17, scale=6),
            existing_nullable=True,
        )

    op.add_column(
        "human_task",
        sa.Column("task_id", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "human_task",
        sa.Column("form_file_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "human_task",
        sa.Column("ui_form_file_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "human_task", sa.Column("updated_at_in_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "human_task", sa.Column("created_at_in_seconds", sa.Integer(), nullable=True)
    )
    with op.batch_alter_table("human_task", schema=None) as batch_op:
        batch_op.alter_column(
            "lane_assignment_id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )

    now_in_seconds = 0
    bind.execute(
        sa.text("UPDATE m8flow_tenant SET status = COALESCE(status, 'ACTIVE')")
    )
    bind.execute(
        sa.text(
            "UPDATE m8flow_tenant SET created_by = COALESCE(created_by, 'system'), "
            "modified_by = COALESCE(modified_by, 'system')"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE m8flow_tenant "
            "SET created_at_in_seconds = COALESCE(created_at_in_seconds, :now), "
            "updated_at_in_seconds = COALESCE(updated_at_in_seconds, :now)"
        ),
        {"now": now_in_seconds},
    )
    bind.execute(
        sa.text("UPDATE human_task SET task_id = COALESCE(task_id, task_guid)")
    )
    bind.execute(
        sa.text(
            "UPDATE human_task "
            "SET created_at_in_seconds = COALESCE(created_at_in_seconds, 0), "
            "updated_at_in_seconds = COALESCE(updated_at_in_seconds, 0)"
        )
    )

    with op.batch_alter_table("m8flow_tenant", schema=None) as batch_op:
        batch_op.alter_column("status", nullable=False)
        batch_op.alter_column("created_at_in_seconds", nullable=False)
        batch_op.alter_column("updated_at_in_seconds", nullable=False)
        batch_op.alter_column("created_by", nullable=False)
        batch_op.alter_column("modified_by", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("m8flow_tenant", schema=None) as batch_op:
        batch_op.drop_column("modified_by")
        batch_op.drop_column("created_by")
        batch_op.drop_column("updated_at_in_seconds")
        batch_op.drop_column("created_at_in_seconds")
        batch_op.drop_column("status")

    op.drop_column("user", "tenant_specific_field_3")
    op.drop_column("user", "tenant_specific_field_2")
    op.drop_column("user", "tenant_specific_field_1")

    op.drop_column("bpmn_process", "end_in_seconds")
    op.drop_column("bpmn_process", "start_in_seconds")

    with op.batch_alter_table("process_instance", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_process_instance_last_milestone_bpmn_name"))
        batch_op.drop_index(batch_op.f("ix_process_instance_bpmn_version_id"))
        batch_op.drop_constraint(
            batch_op.f("fk_process_instance_bpmn_version_id_process_model_bpmn_version"),
            type_="foreignkey",
        )

    op.drop_column("process_instance", "bpmn_version_id")
    op.drop_column("process_instance", "last_milestone_bpmn_name")
    op.drop_column("process_instance", "bpmn_version_control_identifier")
    op.drop_column("process_instance", "bpmn_version_control_type")
    op.drop_column("process_instance", "task_updated_at_in_seconds")
    op.drop_column("process_instance", "spiff_serializer_version")

    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.alter_column(
            "end_in_seconds",
            existing_type=sa.Numeric(precision=17, scale=6),
            type_=sa.Integer(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "start_in_seconds",
            existing_type=sa.Numeric(precision=17, scale=6),
            type_=sa.Integer(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.String(length=10),
            type_=sa.String(length=20),
            existing_nullable=False,
        )

    with op.batch_alter_table("human_task", schema=None) as batch_op:
        batch_op.alter_column(
            "lane_assignment_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )

    op.drop_column("human_task", "created_at_in_seconds")
    op.drop_column("human_task", "updated_at_in_seconds")
    op.drop_column("human_task", "ui_form_file_name")
    op.drop_column("human_task", "form_file_name")
    op.drop_column("human_task", "task_id")

    with op.batch_alter_table("process_model_bpmn_version", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_process_model_bpmn_version_process_model_identifier")
        )
        batch_op.drop_index(batch_op.f("ix_process_model_bpmn_version_m8f_tenant_id"))
        batch_op.drop_index(
            batch_op.f("ix_process_model_bpmn_version_created_at_in_seconds")
        )
        batch_op.drop_index(batch_op.f("ix_process_model_bpmn_version_bpmn_xml_hash"))
    op.drop_table("process_model_bpmn_version")

    op.drop_table("json_data")

    with op.batch_alter_table("group", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_group_source_is_open_id"))
        batch_op.drop_index(batch_op.f("ix_group_name"))
        batch_op.drop_index(batch_op.f("ix_group_identifier"))
    op.drop_table("group")

    TENANT_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
