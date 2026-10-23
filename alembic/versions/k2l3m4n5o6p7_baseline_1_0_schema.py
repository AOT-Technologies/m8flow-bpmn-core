"""m8flow-core 1.0 baseline schema."""

import sqlalchemy as sa
from alembic import op

revision = "k2l3m4n5o6p7"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "m8f_group",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("identifier", sa.String(length=255), nullable=True),
        sa.Column("authorization_key", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_m8f_group")),
        sa.UniqueConstraint("authorization_key", name="m8f_group_authorization_key"),
    )
    op.create_index(
        op.f("ix_m8f_group_authorization_key"),
        "m8f_group",
        ["authorization_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_m8f_group_identifier"), "m8f_group", ["identifier"], unique=False
    )
    op.create_index(op.f("ix_m8f_group_name"), "m8f_group", ["name"], unique=False)
    op.create_table(
        "m8flow_tenant",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "INACTIVE", "DELETED", name="tenantstatus"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("modified_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_m8flow_tenant")),
    )
    op.create_index(
        op.f("ix_m8flow_tenant_slug"), "m8flow_tenant", ["slug"], unique=True
    )
    op.create_table(
        "permission_target",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("command", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "resource_type IN ('process_definition', 'process_instance', "
            "'process_model', 'task', 'tenant')",
            name="m8f_permission_target_resource_type_check",
        ),
        sa.CheckConstraint(
            "resource_type IS NOT NULL AND "
            "(resource_id IS NULL OR length(trim(resource_id)) > 0)",
            name="m8f_permission_target_resource_pair_check",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_target")),
        sa.UniqueConstraint(
            "resource_type",
            "resource_id",
            "command",
            name="m8f_permission_target_resource_command_key",
        ),
    )
    op.create_index(
        op.f("ix_permission_target_command"),
        "permission_target",
        ["command"],
        unique=False,
    )
    op.create_index(
        op.f("ix_permission_target_resource_id"),
        "permission_target",
        ["resource_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_permission_target_resource_type"),
        "permission_target",
        ["resource_type"],
        unique=False,
    )
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("service", sa.String(length=255), nullable=False),
        sa.Column("service_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("realm_identifier", sa.String(length=255), nullable=True),
        sa.Column("external_org_id", sa.String(length=255), nullable=True),
        sa.Column("external_user_id", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("service", "service_id", name="m8f_user_service_id_key"),
    )
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=False)
    op.create_index(op.f("ix_user_service"), "user", ["service"], unique=False)
    op.create_index(op.f("ix_user_service_id"), "user", ["service_id"], unique=False)
    op.create_index(op.f("ix_user_username"), "user", ["username"], unique=False)
    op.create_table(
        "bpmn_process_definition",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("process_xml_digest", sa.String(length=255), nullable=False),
        sa.Column("bpmn_identifier", sa.String(length=255), nullable=False),
        sa.Column("bpmn_name", sa.String(length=255), nullable=True),
        sa.Column("properties_json", sa.JSON(), nullable=False),
        sa.Column("bpmn_version_control_type", sa.String(length=50), nullable=True),
        sa.Column(
            "bpmn_version_control_identifier", sa.String(length=255), nullable=True
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_bpmn_process_definition_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bpmn_process_definition")),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "process_xml_digest",
            name="m8f_bpmn_process_definition_digest_tenant_key",
        ),
    )
    op.create_index(
        op.f("ix_bpmn_process_definition_bpmn_identifier"),
        "bpmn_process_definition",
        ["bpmn_identifier"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bpmn_process_definition_bpmn_name"),
        "bpmn_process_definition",
        ["bpmn_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bpmn_process_definition_m8f_tenant_id"),
        "bpmn_process_definition",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_table(
        "json_data",
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.Column("hash", sa.String(length=255), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_json_data_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.PrimaryKeyConstraint("m8f_tenant_id", "hash", name=op.f("pk_json_data")),
    )
    op.create_table(
        "principal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR "
            "(user_id IS NULL AND group_id IS NOT NULL)",
            name="m8f_principal_exactly_one_subject",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["m8f_group.id"], name=op.f("fk_principal_group_id_m8f_group")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_principal_user_id_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_principal")),
    )
    op.create_index(
        op.f("ix_principal_group_id"), "principal", ["group_id"], unique=True
    )
    op.create_index(op.f("ix_principal_user_id"), "principal", ["user_id"], unique=True)
    op.create_table(
        "process_model_bpmn_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("process_model_identifier", sa.String(length=255), nullable=False),
        sa.Column("bpmn_xml_hash", sa.String(length=64), nullable=False),
        sa.Column("bpmn_xml_file_contents", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_process_model_bpmn_version_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_process_model_bpmn_version")),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "process_model_identifier",
            "bpmn_xml_hash",
            name="uq_process_model_bpmn_version_tenant_model_hash",
        ),
    )
    op.create_index(
        op.f("ix_process_model_bpmn_version_bpmn_xml_hash"),
        "process_model_bpmn_version",
        ["bpmn_xml_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_model_bpmn_version_created_at"),
        "process_model_bpmn_version",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_model_bpmn_version_m8f_tenant_id"),
        "process_model_bpmn_version",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_model_bpmn_version_process_model_identifier"),
        "process_model_bpmn_version",
        ["process_model_identifier"],
        unique=False,
    )
    op.create_table(
        "user_group_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["m8f_group.id"],
            name=op.f("fk_user_group_assignment_group_id_m8f_group"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_user_group_assignment_user_id_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_group_assignment")),
        sa.UniqueConstraint(
            "user_id", "group_id", name="m8f_user_group_assignment_user_group_key"
        ),
    )
    op.create_index(
        op.f("ix_user_group_assignment_group_id"),
        "user_group_assignment",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_group_assignment_user_id"),
        "user_group_assignment",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "bpmn_process",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guid", sa.String(length=36), nullable=True),
        sa.Column("bpmn_process_definition_id", sa.Integer(), nullable=False),
        sa.Column("top_level_process_id", sa.Integer(), nullable=True),
        sa.Column("direct_parent_process_id", sa.Integer(), nullable=True),
        sa.Column("properties_json", sa.JSON(), nullable=False),
        sa.Column("json_data_hash", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["bpmn_process_definition_id"],
            ["bpmn_process_definition.id"],
            name=op.f(
                "fk_bpmn_process_bpmn_process_definition_id_bpmn_process_definition"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["direct_parent_process_id"],
            ["bpmn_process.id"],
            name=op.f("fk_bpmn_process_direct_parent_process_id_bpmn_process"),
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_bpmn_process_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["top_level_process_id"],
            ["bpmn_process.id"],
            name=op.f("fk_bpmn_process_top_level_process_id_bpmn_process"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bpmn_process")),
        sa.UniqueConstraint("guid", name=op.f("uq_bpmn_process_guid")),
    )
    op.create_index(
        op.f("ix_bpmn_process_bpmn_process_definition_id"),
        "bpmn_process",
        ["bpmn_process_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bpmn_process_direct_parent_process_id"),
        "bpmn_process",
        ["direct_parent_process_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bpmn_process_json_data_hash"),
        "bpmn_process",
        ["json_data_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bpmn_process_m8f_tenant_id"),
        "bpmn_process",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bpmn_process_top_level_process_id"),
        "bpmn_process",
        ["top_level_process_id"],
        unique=False,
    )
    op.create_table(
        "permission_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("principal_id", sa.Integer(), nullable=False),
        sa.Column("permission_target_id", sa.Integer(), nullable=False),
        sa.Column("grant_type", sa.String(length=50), nullable=False),
        sa.Column("permission", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_target_id"],
            ["permission_target.id"],
            name=op.f(
                "fk_permission_assignment_permission_target_id_permission_target"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principal.id"],
            name=op.f("fk_permission_assignment_principal_id_principal"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_assignment")),
        sa.UniqueConstraint(
            "principal_id",
            "permission_target_id",
            "permission",
            name="m8f_permission_assignment_principal_target_permission_key",
        ),
    )
    op.create_index(
        op.f("ix_permission_assignment_permission_target_id"),
        "permission_assignment",
        ["permission_target_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_permission_assignment_principal_id"),
        "permission_assignment",
        ["principal_id"],
        unique=False,
    )
    op.create_table(
        "task_definition",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bpmn_process_definition_id", sa.Integer(), nullable=False),
        sa.Column("bpmn_identifier", sa.String(length=255), nullable=False),
        sa.Column("bpmn_name", sa.String(length=255), nullable=True),
        sa.Column("typename", sa.String(length=255), nullable=False),
        sa.Column("properties_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["bpmn_process_definition_id"],
            ["bpmn_process_definition.id"],
            name=op.f(
                "fk_task_definition_bpmn_process_definition_id_bpmn_process_definition"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_task_definition_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_definition")),
        sa.UniqueConstraint(
            "m8f_tenant_id",
            "bpmn_process_definition_id",
            "bpmn_identifier",
            name="m8f_task_definition_tenant_process_key",
        ),
    )
    op.create_index(
        op.f("ix_task_definition_bpmn_identifier"),
        "task_definition",
        ["bpmn_identifier"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_definition_bpmn_name"),
        "task_definition",
        ["bpmn_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_definition_bpmn_process_definition_id"),
        "task_definition",
        ["bpmn_process_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_definition_m8f_tenant_id"),
        "task_definition",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_definition_typename"),
        "task_definition",
        ["typename"],
        unique=False,
    )
    op.create_table(
        "process_instance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("process_model_identifier", sa.String(length=255), nullable=False),
        sa.Column("process_model_display_name", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.String(length=255), nullable=True),
        sa.Column("process_initiator_id", sa.Integer(), nullable=False),
        sa.Column("bpmn_process_definition_id", sa.Integer(), nullable=True),
        sa.Column("bpmn_process_id", sa.Integer(), nullable=True),
        sa.Column("workflow_engine_version", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("task_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bpmn_version_control_type", sa.String(length=50), nullable=True),
        sa.Column(
            "bpmn_version_control_identifier", sa.String(length=255), nullable=True
        ),
        sa.Column("last_milestone_bpmn_name", sa.String(length=255), nullable=True),
        sa.Column("bpmn_version_id", sa.Integer(), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["bpmn_process_definition_id"],
            ["bpmn_process_definition.id"],
            name=op.f(
                "fk_process_instance_bpmn_process_definition_id_bpmn_process_definition"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["bpmn_process_id"],
            ["bpmn_process.id"],
            name=op.f("fk_process_instance_bpmn_process_id_bpmn_process"),
        ),
        sa.ForeignKeyConstraint(
            ["bpmn_version_id"],
            ["process_model_bpmn_version.id"],
            name=op.f("fk_process_instance_bpmn_version_id_process_model_bpmn_version"),
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_process_instance_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_initiator_id"],
            ["user.id"],
            name=op.f("fk_process_instance_process_initiator_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_process_instance")),
    )
    op.create_index(
        op.f("ix_process_instance_bpmn_process_definition_id"),
        "process_instance",
        ["bpmn_process_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_bpmn_process_id"),
        "process_instance",
        ["bpmn_process_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_bpmn_version_id"),
        "process_instance",
        ["bpmn_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_last_milestone_bpmn_name"),
        "process_instance",
        ["last_milestone_bpmn_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_m8f_tenant_id"),
        "process_instance",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_process_initiator_id"),
        "process_instance",
        ["process_initiator_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_process_model_display_name"),
        "process_instance",
        ["process_model_display_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_process_model_identifier"),
        "process_instance",
        ["process_model_identifier"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_status"), "process_instance", ["status"], unique=False
    )
    op.create_table(
        "process_instance_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_guid", sa.String(length=36), nullable=True),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "category IN ('process', 'task')",
            name="m8f_process_instance_event_category_check",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_process_instance_event_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name=op.f("fk_process_instance_event_process_instance_id_process_instance"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_process_instance_event_user_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_process_instance_event")),
    )
    op.create_index(
        op.f("ix_process_instance_event_category"),
        "process_instance_event",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_event_event_type"),
        "process_instance_event",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_event_m8f_tenant_id"),
        "process_instance_event",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_event_occurred_at"),
        "process_instance_event",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_event_process_instance_id"),
        "process_instance_event",
        ["process_instance_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_event_task_guid"),
        "process_instance_event",
        ["task_guid"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_event_user_id"),
        "process_instance_event",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "process_instance_metadata",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
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
            "process_instance_id", "key", name="m8f_process_instance_metadata_key"
        ),
    )
    op.create_index(
        op.f("ix_process_instance_metadata_key"),
        "process_instance_metadata",
        ["key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_metadata_m8f_tenant_id"),
        "process_instance_metadata",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_process_instance_metadata_process_instance_id"),
        "process_instance_metadata",
        ["process_instance_id"],
        unique=False,
    )
    op.create_table(
        "scheduler_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_key", sa.String(length=255), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=True),
        sa.Column("bpmn_process_definition_id", sa.Integer(), nullable=True),
        sa.Column("locked_by", sa.String(length=255), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["bpmn_process_definition_id"],
            ["bpmn_process_definition.id"],
            name=op.f(
                "fk_scheduler_job_bpmn_process_definition_id_bpmn_process_definition"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_scheduler_job_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name=op.f("fk_scheduler_job_process_instance_id_process_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduler_job")),
        sa.UniqueConstraint(
            "m8f_tenant_id", "job_key", name="uq_scheduler_job_tenant_job_key"
        ),
    )
    op.create_index(
        op.f("ix_scheduler_job_bpmn_process_definition_id"),
        "scheduler_job",
        ["bpmn_process_definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduler_job_job_type"), "scheduler_job", ["job_type"], unique=False
    )
    op.create_index(
        op.f("ix_scheduler_job_locked_at"), "scheduler_job", ["locked_at"], unique=False
    )
    op.create_index(
        op.f("ix_scheduler_job_locked_by"), "scheduler_job", ["locked_by"], unique=False
    )
    op.create_index(
        op.f("ix_scheduler_job_m8f_tenant_id"),
        "scheduler_job",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduler_job_process_instance_id"),
        "scheduler_job",
        ["process_instance_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduler_job_run_at"), "scheduler_job", ["run_at"], unique=False
    )
    op.create_table(
        "task",
        sa.Column("guid", sa.String(length=36), nullable=False),
        sa.Column("bpmn_process_id", sa.Integer(), nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.Column("task_definition_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("properties_json", sa.JSON(), nullable=False),
        sa.Column("json_data_hash", sa.String(length=255), nullable=False),
        sa.Column("python_env_data_hash", sa.String(length=255), nullable=False),
        sa.Column("runtime_info", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["bpmn_process_id"],
            ["bpmn_process.id"],
            name=op.f("fk_task_bpmn_process_id_bpmn_process"),
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_task_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name=op.f("fk_task_process_instance_id_process_instance"),
        ),
        sa.ForeignKeyConstraint(
            ["task_definition_id"],
            ["task_definition.id"],
            name=op.f("fk_task_task_definition_id_task_definition"),
        ),
        sa.PrimaryKeyConstraint("guid", name=op.f("pk_task")),
    )
    op.create_index(
        op.f("ix_task_bpmn_process_id"), "task", ["bpmn_process_id"], unique=False
    )
    op.create_index(
        op.f("ix_task_json_data_hash"), "task", ["json_data_hash"], unique=False
    )
    op.create_index(
        op.f("ix_task_m8f_tenant_id"), "task", ["m8f_tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_task_process_instance_id"),
        "task",
        ["process_instance_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_python_env_data_hash"),
        "task",
        ["python_env_data_hash"],
        unique=False,
    )
    op.create_index(op.f("ix_task_state"), "task", ["state"], unique=False)
    op.create_index(
        op.f("ix_task_task_definition_id"), "task", ["task_definition_id"], unique=False
    )
    op.create_table(
        "future_task",
        sa.Column("guid", sa.String(length=36), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_to_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column(
            "archived_for_process_instance_status",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["guid"],
            ["task.guid"],
            name="m8f_future_task_task_guid_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_future_task_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.PrimaryKeyConstraint("guid", name=op.f("pk_future_task")),
    )
    op.create_index(
        op.f("ix_future_task_archived_for_process_instance_status"),
        "future_task",
        ["archived_for_process_instance_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_future_task_completed"), "future_task", ["completed"], unique=False
    )
    op.create_index(
        op.f("ix_future_task_m8f_tenant_id"),
        "future_task",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_future_task_queued_to_run_at"),
        "future_task",
        ["queued_to_run_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_future_task_run_at"), "future_task", ["run_at"], unique=False
    )
    op.create_table(
        "work_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("process_instance_id", sa.Integer(), nullable=False),
        sa.Column("task_guid", sa.String(length=36), nullable=True),
        sa.Column("lane_assignment_id", sa.Integer(), nullable=True),
        sa.Column("completed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("actual_owner_id", sa.Integer(), nullable=True),
        sa.Column("task_status", sa.String(length=50), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["actual_owner_id"],
            ["user.id"],
            name=op.f("fk_work_item_actual_owner_id_user"),
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_user_id"],
            ["user.id"],
            name=op.f("fk_work_item_completed_by_user_id_user"),
        ),
        sa.ForeignKeyConstraint(
            ["lane_assignment_id"],
            ["m8f_group.id"],
            name=op.f("fk_work_item_lane_assignment_id_m8f_group"),
        ),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_work_item_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["process_instance_id"],
            ["process_instance.id"],
            name=op.f("fk_work_item_process_instance_id_process_instance"),
        ),
        sa.ForeignKeyConstraint(
            ["task_guid"], ["task.guid"], name=op.f("fk_work_item_task_guid_task")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_item")),
        sa.UniqueConstraint("task_guid", name="m8f_work_item_task_guid_key"),
    )
    op.create_index(
        op.f("ix_work_item_actual_owner_id"),
        "work_item",
        ["actual_owner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_work_item_completed"), "work_item", ["completed"], unique=False
    )
    op.create_index(
        op.f("ix_work_item_completed_by_user_id"),
        "work_item",
        ["completed_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_work_item_lane_assignment_id"),
        "work_item",
        ["lane_assignment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_work_item_m8f_tenant_id"), "work_item", ["m8f_tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_work_item_process_instance_id"),
        "work_item",
        ["process_instance_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_work_item_task_guid"), "work_item", ["task_guid"], unique=False
    )
    op.create_table(
        "work_item_user",
        sa.Column("work_item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("added_by", sa.String(length=20), nullable=True),
        sa.Column("m8f_tenant_id", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["m8f_tenant_id"],
            ["m8flow_tenant.id"],
            name=op.f("fk_work_item_user_m8f_tenant_id_m8flow_tenant"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_work_item_user_user_id_user")
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_item.id"],
            name=op.f("fk_work_item_user_work_item_id_work_item"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "work_item_id", "user_id", name=op.f("pk_work_item_user")
        ),
        sa.UniqueConstraint("work_item_id", "user_id", name="m8f_work_item_user_key"),
    )
    op.create_index(
        op.f("ix_work_item_user_added_by"), "work_item_user", ["added_by"], unique=False
    )
    op.create_index(
        op.f("ix_work_item_user_m8f_tenant_id"),
        "work_item_user",
        ["m8f_tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_work_item_user_user_id"), "work_item_user", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_work_item_user_user_id"), table_name="work_item_user")
    op.drop_index(op.f("ix_work_item_user_m8f_tenant_id"), table_name="work_item_user")
    op.drop_index(op.f("ix_work_item_user_added_by"), table_name="work_item_user")
    op.drop_table("work_item_user")
    op.drop_index(op.f("ix_work_item_task_guid"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_process_instance_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_m8f_tenant_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_lane_assignment_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_completed_by_user_id"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_completed"), table_name="work_item")
    op.drop_index(op.f("ix_work_item_actual_owner_id"), table_name="work_item")
    op.drop_table("work_item")
    op.drop_index(op.f("ix_future_task_run_at"), table_name="future_task")
    op.drop_index(op.f("ix_future_task_queued_to_run_at"), table_name="future_task")
    op.drop_index(op.f("ix_future_task_m8f_tenant_id"), table_name="future_task")
    op.drop_index(op.f("ix_future_task_completed"), table_name="future_task")
    op.drop_index(
        op.f("ix_future_task_archived_for_process_instance_status"),
        table_name="future_task",
    )
    op.drop_table("future_task")
    op.drop_index(op.f("ix_task_task_definition_id"), table_name="task")
    op.drop_index(op.f("ix_task_state"), table_name="task")
    op.drop_index(op.f("ix_task_python_env_data_hash"), table_name="task")
    op.drop_index(op.f("ix_task_process_instance_id"), table_name="task")
    op.drop_index(op.f("ix_task_m8f_tenant_id"), table_name="task")
    op.drop_index(op.f("ix_task_json_data_hash"), table_name="task")
    op.drop_index(op.f("ix_task_bpmn_process_id"), table_name="task")
    op.drop_table("task")
    op.drop_index(op.f("ix_scheduler_job_run_at"), table_name="scheduler_job")
    op.drop_index(
        op.f("ix_scheduler_job_process_instance_id"), table_name="scheduler_job"
    )
    op.drop_index(op.f("ix_scheduler_job_m8f_tenant_id"), table_name="scheduler_job")
    op.drop_index(op.f("ix_scheduler_job_locked_by"), table_name="scheduler_job")
    op.drop_index(op.f("ix_scheduler_job_locked_at"), table_name="scheduler_job")
    op.drop_index(op.f("ix_scheduler_job_job_type"), table_name="scheduler_job")
    op.drop_index(
        op.f("ix_scheduler_job_bpmn_process_definition_id"), table_name="scheduler_job"
    )
    op.drop_table("scheduler_job")
    op.drop_index(
        op.f("ix_process_instance_metadata_process_instance_id"),
        table_name="process_instance_metadata",
    )
    op.drop_index(
        op.f("ix_process_instance_metadata_m8f_tenant_id"),
        table_name="process_instance_metadata",
    )
    op.drop_index(
        op.f("ix_process_instance_metadata_key"), table_name="process_instance_metadata"
    )
    op.drop_table("process_instance_metadata")
    op.drop_index(
        op.f("ix_process_instance_event_user_id"), table_name="process_instance_event"
    )
    op.drop_index(
        op.f("ix_process_instance_event_task_guid"), table_name="process_instance_event"
    )
    op.drop_index(
        op.f("ix_process_instance_event_process_instance_id"),
        table_name="process_instance_event",
    )
    op.drop_index(
        op.f("ix_process_instance_event_occurred_at"),
        table_name="process_instance_event",
    )
    op.drop_index(
        op.f("ix_process_instance_event_m8f_tenant_id"),
        table_name="process_instance_event",
    )
    op.drop_index(
        op.f("ix_process_instance_event_event_type"),
        table_name="process_instance_event",
    )
    op.drop_index(
        op.f("ix_process_instance_event_category"), table_name="process_instance_event"
    )
    op.drop_table("process_instance_event")
    op.drop_index(op.f("ix_process_instance_status"), table_name="process_instance")
    op.drop_index(
        op.f("ix_process_instance_process_model_identifier"),
        table_name="process_instance",
    )
    op.drop_index(
        op.f("ix_process_instance_process_model_display_name"),
        table_name="process_instance",
    )
    op.drop_index(
        op.f("ix_process_instance_process_initiator_id"), table_name="process_instance"
    )
    op.drop_index(
        op.f("ix_process_instance_m8f_tenant_id"), table_name="process_instance"
    )
    op.drop_index(
        op.f("ix_process_instance_last_milestone_bpmn_name"),
        table_name="process_instance",
    )
    op.drop_index(
        op.f("ix_process_instance_bpmn_version_id"), table_name="process_instance"
    )
    op.drop_index(
        op.f("ix_process_instance_bpmn_process_id"), table_name="process_instance"
    )
    op.drop_index(
        op.f("ix_process_instance_bpmn_process_definition_id"),
        table_name="process_instance",
    )
    op.drop_table("process_instance")
    op.drop_index(op.f("ix_task_definition_typename"), table_name="task_definition")
    op.drop_index(
        op.f("ix_task_definition_m8f_tenant_id"), table_name="task_definition"
    )
    op.drop_index(
        op.f("ix_task_definition_bpmn_process_definition_id"),
        table_name="task_definition",
    )
    op.drop_index(op.f("ix_task_definition_bpmn_name"), table_name="task_definition")
    op.drop_index(
        op.f("ix_task_definition_bpmn_identifier"), table_name="task_definition"
    )
    op.drop_table("task_definition")
    op.drop_index(
        op.f("ix_permission_assignment_principal_id"),
        table_name="permission_assignment",
    )
    op.drop_index(
        op.f("ix_permission_assignment_permission_target_id"),
        table_name="permission_assignment",
    )
    op.drop_table("permission_assignment")
    op.drop_index(
        op.f("ix_bpmn_process_top_level_process_id"), table_name="bpmn_process"
    )
    op.drop_index(op.f("ix_bpmn_process_m8f_tenant_id"), table_name="bpmn_process")
    op.drop_index(op.f("ix_bpmn_process_json_data_hash"), table_name="bpmn_process")
    op.drop_index(
        op.f("ix_bpmn_process_direct_parent_process_id"), table_name="bpmn_process"
    )
    op.drop_index(
        op.f("ix_bpmn_process_bpmn_process_definition_id"), table_name="bpmn_process"
    )
    op.drop_table("bpmn_process")
    op.drop_index(
        op.f("ix_user_group_assignment_user_id"), table_name="user_group_assignment"
    )
    op.drop_index(
        op.f("ix_user_group_assignment_group_id"), table_name="user_group_assignment"
    )
    op.drop_table("user_group_assignment")
    op.drop_index(
        op.f("ix_process_model_bpmn_version_process_model_identifier"),
        table_name="process_model_bpmn_version",
    )
    op.drop_index(
        op.f("ix_process_model_bpmn_version_m8f_tenant_id"),
        table_name="process_model_bpmn_version",
    )
    op.drop_index(
        op.f("ix_process_model_bpmn_version_created_at"),
        table_name="process_model_bpmn_version",
    )
    op.drop_index(
        op.f("ix_process_model_bpmn_version_bpmn_xml_hash"),
        table_name="process_model_bpmn_version",
    )
    op.drop_table("process_model_bpmn_version")
    op.drop_index(op.f("ix_principal_user_id"), table_name="principal")
    op.drop_index(op.f("ix_principal_group_id"), table_name="principal")
    op.drop_table("principal")
    op.drop_table("json_data")
    op.drop_index(
        op.f("ix_bpmn_process_definition_m8f_tenant_id"),
        table_name="bpmn_process_definition",
    )
    op.drop_index(
        op.f("ix_bpmn_process_definition_bpmn_name"),
        table_name="bpmn_process_definition",
    )
    op.drop_index(
        op.f("ix_bpmn_process_definition_bpmn_identifier"),
        table_name="bpmn_process_definition",
    )
    op.drop_table("bpmn_process_definition")
    op.drop_index(op.f("ix_user_username"), table_name="user")
    op.drop_index(op.f("ix_user_service_id"), table_name="user")
    op.drop_index(op.f("ix_user_service"), table_name="user")
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_table("user")
    op.drop_index(
        op.f("ix_permission_target_resource_type"), table_name="permission_target"
    )
    op.drop_index(
        op.f("ix_permission_target_resource_id"), table_name="permission_target"
    )
    op.drop_index(op.f("ix_permission_target_command"), table_name="permission_target")
    op.drop_table("permission_target")
    op.drop_index(op.f("ix_m8flow_tenant_slug"), table_name="m8flow_tenant")
    op.drop_table("m8flow_tenant")
    op.drop_index(op.f("ix_m8f_group_name"), table_name="m8f_group")
    op.drop_index(op.f("ix_m8f_group_identifier"), table_name="m8f_group")
    op.drop_index(op.f("ix_m8f_group_authorization_key"), table_name="m8f_group")
    op.drop_table("m8f_group")
    sa.Enum(name="tenantstatus").drop(op.get_bind(), checkfirst=True)
