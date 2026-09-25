from __future__ import annotations

from dataclasses import fields

from m8flow_bpmn_core.application.commands import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    CreateProcessInstanceCommand,
    ImportBpmnProcessDefinitionCommand,
    InitializeProcessInstanceFromDefinitionCommand,
    InitializeProcessInstanceWorkflowCommand,
    RecordProcessInstanceEventCommand,
    UpsertProcessInstanceMetadataCommand,
)
from m8flow_bpmn_core.application.queries import (
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    ListProcessInstancesQuery,
)
from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.human_task import HumanTaskModel
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import (
    ProcessInstanceEventModel,
    ProcessInstanceEventType,
)

EXPECTED_COMMAND_FIELDS = {
    ClaimTaskCommand: ["tenant_id", "human_task_id", "user_id", "added_by"],
    CompleteTaskCommand: [
        "tenant_id",
        "human_task_id",
        "user_id",
        "completed_at_in_seconds",
        "task_payload",
    ],
    CreateProcessInstanceCommand: [
        "tenant_id",
        "process_model_identifier",
        "process_model_display_name",
        "process_initiator_id",
        "bpmn_process_definition_id",
        "bpmn_process_id",
        "summary",
        "process_version",
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ],
    ImportBpmnProcessDefinitionCommand: [
        "tenant_id",
        "bpmn_identifier",
        "user_id",
        "source_bpmn_xml",
        "source_dmn_xml",
        "bpmn_name",
        "properties_json",
        "bpmn_version_control_type",
        "bpmn_version_control_identifier",
        "single_process_hash",
        "full_process_model_hash",
        "created_at_in_seconds",
        "updated_at_in_seconds",
    ],
    InitializeProcessInstanceFromDefinitionCommand: [
        "tenant_id",
        "bpmn_process_definition_id",
        "process_initiator_id",
        "submission_metadata",
        "summary",
        "process_version",
        "started_at_in_seconds",
        "bpmn_process_id",
    ],
    InitializeProcessInstanceWorkflowCommand: [
        "tenant_id",
        "process_instance_id",
        "bpmn_xml",
        "bpmn_process_id",
        "started_at_in_seconds",
        "dmn_xml",
    ],
    RecordProcessInstanceEventCommand: [
        "tenant_id",
        "process_instance_id",
        "event_type",
        "task_guid",
        "user_id",
        "timestamp",
    ],
    UpsertProcessInstanceMetadataCommand: [
        "tenant_id",
        "process_instance_id",
        "key",
        "value",
        "updated_at_in_seconds",
        "created_at_in_seconds",
    ],
}

EXPECTED_QUERY_FIELDS = {
    GetPendingTasksQuery: ["tenant_id", "user_id"],
    GetProcessInstanceEventsQuery: ["tenant_id", "process_instance_id"],
    GetProcessInstanceMetadataQuery: ["tenant_id", "process_instance_id"],
    GetProcessInstanceQuery: ["tenant_id", "process_instance_id"],
    ListProcessInstancesQuery: ["tenant_id", "status"],
}


def test_public_command_and_query_field_order_is_compatible() -> None:
    for model, expected_fields in EXPECTED_COMMAND_FIELDS.items():
        assert [field.name for field in fields(model)] == expected_fields
        assert fields(model)[0].name == "tenant_id"

    for model, expected_fields in EXPECTED_QUERY_FIELDS.items():
        assert [field.name for field in fields(model)] == expected_fields
        assert fields(model)[0].name == "tenant_id"


def test_public_result_models_retain_response_attributes() -> None:
    assert {
        "id",
        "process_model_identifier",
        "status",
        "start_in_seconds",
        "end_in_seconds",
        "updated_at_in_seconds",
        "created_at_in_seconds",
        "spiff_serializer_version",
    }.issubset(ProcessInstanceModel.__mapper__.attrs.keys())
    assert {
        "id",
        "process_instance_id",
        "task_guid",
        "task_status",
        "completed",
        "actual_owner_id",
        "lane_assignment_id",
        "created_at_in_seconds",
        "updated_at_in_seconds",
    }.issubset(HumanTaskModel.__mapper__.attrs.keys())
    assert {
        "id",
        "process_instance_id",
        "event_type",
        "timestamp",
    }.issubset(ProcessInstanceEventModel.__mapper__.attrs.keys())


def test_native_timestamp_attributes_are_additive() -> None:
    assert {
        "started_at",
        "ended_at",
        "task_updated_at",
        "created_at",
        "updated_at",
    }.issubset(ProcessInstanceModel.__mapper__.attrs.keys())
    assert {"created_at", "updated_at"}.issubset(
        HumanTaskModel.__mapper__.attrs.keys()
    )
    assert "occurred_at" in ProcessInstanceEventModel.__mapper__.attrs


def test_schema_table_names_are_compatible_baseline() -> None:
    expected_tables = {
        "user",
        "group",
        "principal",
        "user_group_assignment",
        "permission_target",
        "permission_assignment",
        "bpmn_process_definition",
        "bpmn_process",
        "process_model_bpmn_version",
        "process_instance",
        "task",
        "task_definition",
        "human_task",
        "human_task_user",
        "future_task",
        "json_data",
        "process_instance_event",
        "process_instance_metadata",
        "scheduler_job",
    }
    assert expected_tables.issubset(Base.metadata.tables)


def test_public_enum_values_are_compatible() -> None:
    assert [event.value for event in ProcessInstanceEventType] == [
        "process_instance_created",
        "process_instance_completed",
        "process_instance_error",
        "process_instance_force_run",
        "process_instance_migrated",
        "process_instance_resumed",
        "process_instance_retried",
        "process_instance_rewound_to_task",
        "process_instance_suspended",
        "process_instance_suspended_for_error",
        "process_instance_terminated",
        "task_cancelled",
        "task_completed",
        "task_data_edited",
        "task_executed_manually",
        "task_failed",
        "task_skipped",
    ]
