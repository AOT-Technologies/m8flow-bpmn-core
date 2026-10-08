from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest
from SpiffWorkflow.util.task import TaskState

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
from m8flow_bpmn_core.models.process_instance import ProcessInstanceModel
from m8flow_bpmn_core.models.process_instance_event import (
    ProcessInstanceEventCategory,
    ProcessInstanceEventModel,
    ProcessLifecycleEventType,
    TaskEventType,
)
from m8flow_bpmn_core.models.task import (
    M8F_TERMINATED_TASK_STATE,
    TaskModel,
)
from m8flow_bpmn_core.models.work_item import WorkItemModel
from m8flow_bpmn_core.services.work_items import WorkItemState

EXPECTED_COMMAND_FIELDS = {
    ClaimTaskCommand: ["tenant_id", "work_item_id", "user_id", "added_by"],
    CompleteTaskCommand: [
        "tenant_id",
        "work_item_id",
        "user_id",
        "completed_at",
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
        "created_at",
        "updated_at",
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
        "process_xml_digest",
        "created_at",
        "updated_at",
    ],
    InitializeProcessInstanceFromDefinitionCommand: [
        "tenant_id",
        "bpmn_process_definition_id",
        "process_initiator_id",
        "submission_metadata",
        "summary",
        "process_version",
        "started_at",
        "bpmn_process_id",
    ],
    InitializeProcessInstanceWorkflowCommand: [
        "tenant_id",
        "process_instance_id",
        "bpmn_xml",
        "bpmn_process_id",
        "started_at",
        "dmn_xml",
    ],
    RecordProcessInstanceEventCommand: [
        "tenant_id",
        "process_instance_id",
        "event_type",
        "task_guid",
        "user_id",
        "occurred_at",
    ],
    UpsertProcessInstanceMetadataCommand: [
        "tenant_id",
        "process_instance_id",
        "key",
        "value",
        "updated_at",
        "created_at",
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
        "started_at",
        "ended_at",
        "updated_at",
        "created_at",
        "workflow_engine_version",
    }.issubset(ProcessInstanceModel.__mapper__.attrs.keys())
    assert {
        "id",
        "process_instance_id",
        "task_guid",
        "task_status",
        "completed",
        "actual_owner_id",
        "lane_assignment_id",
        "created_at",
        "updated_at",
    }.issubset(WorkItemModel.__mapper__.attrs.keys())
    assert {
        "id",
        "process_instance_id",
        "event_type",
        "occurred_at",
    }.issubset(ProcessInstanceEventModel.__mapper__.attrs.keys())


def test_native_occurred_at_attributes_are_additive() -> None:
    assert {
        "started_at",
        "ended_at",
        "task_updated_at",
        "created_at",
        "updated_at",
    }.issubset(ProcessInstanceModel.__mapper__.attrs.keys())
    assert {"created_at", "updated_at"}.issubset(
        WorkItemModel.__mapper__.attrs.keys()
    )
    assert "occurred_at" in ProcessInstanceEventModel.__mapper__.attrs


def test_schema_table_names_are_compatible_baseline() -> None:
    expected_tables = {
        "user",
        "m8f_group",
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
        "work_item",
        "work_item_user",
        "future_task",
        "json_data",
        "process_instance_event",
        "process_instance_metadata",
        "scheduler_job",
    }
    assert expected_tables.issubset(Base.metadata.tables)


def test_event_enums_are_split_without_changing_persisted_values() -> None:
    assert [event.value for event in ProcessLifecycleEventType] == [
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
    ]
    assert [event.value for event in TaskEventType] == [
        "task_cancelled",
        "task_completed",
        "task_data_edited",
        "task_executed_manually",
        "task_failed",
        "task_skipped",
    ]

    process_event = ProcessInstanceEventModel(
        event_type=ProcessLifecycleEventType.process_instance_created,
        process_instance_id=1,
        occurred_at=datetime.fromtimestamp(1, UTC),
    )
    task_event = ProcessInstanceEventModel(
        event_type=TaskEventType.task_completed,
        process_instance_id=1,
        occurred_at=datetime.fromtimestamp(1, UTC),
    )
    assert process_event.category == ProcessInstanceEventCategory.process.value
    assert task_event.category == ProcessInstanceEventCategory.task.value

    with pytest.raises(ValueError, match="does not match event type"):
        ProcessInstanceEventModel(
            event_type=TaskEventType.task_completed,
            category=ProcessInstanceEventCategory.process,
            process_instance_id=1,
            occurred_at=datetime.fromtimestamp(1, UTC),
        )

    with pytest.raises(ValueError, match="Invalid process instance event category"):
        ProcessInstanceEventModel(
            event_type=TaskEventType.task_completed,
            category="invalid",
            process_instance_id=1,
            occurred_at=datetime.fromtimestamp(1, UTC),
        )


def test_task_state_validation_uses_spiff_names_and_preserves_termination() -> None:
    assert TaskModel(state=TaskState.READY).state == "READY"
    assert TaskModel(state="COMPLETED").state == TaskState.get_name(
        TaskState.COMPLETED
    )
    assert TaskModel(state=M8F_TERMINATED_TASK_STATE).state == (
        M8F_TERMINATED_TASK_STATE
    )

    with pytest.raises(ValueError):
        TaskModel(state="not-a-task-state")


def test_work_item_termination_is_not_a_spiff_task_state() -> None:
    assert WorkItemState.TERMINATED.value == M8F_TERMINATED_TASK_STATE
