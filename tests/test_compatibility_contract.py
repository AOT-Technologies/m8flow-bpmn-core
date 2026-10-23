from __future__ import annotations

import re
from dataclasses import fields
from datetime import UTC, datetime

import pytest
from SpiffWorkflow.util.task import TaskState

from m8flow_core.application.commands import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    CreateProcessInstanceCommand,
    ImportBpmnProcessDefinitionCommand,
    InitializeProcessInstanceFromDefinitionCommand,
    InitializeProcessInstanceWorkflowCommand,
    RecordProcessInstanceEventCommand,
    UpsertProcessInstanceMetadataCommand,
)
from m8flow_core.application.queries import (
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    ListProcessInstancesQuery,
)
from m8flow_core.errors import ValidationError
from m8flow_core.models.base import Base
from m8flow_core.models.future_task import FutureTaskModel
from m8flow_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_core.models.process_instance_event import (
    ProcessInstanceEventCategory,
    ProcessInstanceEventModel,
    ProcessLifecycleEventType,
    TaskEventType,
    event_category_for_type,
)
from m8flow_core.models.task import (
    M8F_TERMINATED_TASK_STATE,
    TaskModel,
)
from m8flow_core.models.work_item import WorkItemModel
from m8flow_core.services.work_items import WorkItemState

EXPECTED_COMMAND_FIELDS = {
    ClaimTaskCommand: ["tenant_id", "work_item_id", "user_id"],
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


PROCESS_EVENT_TYPES = {
    "process_instance_created",
    "process_instance_suspended",
    "process_instance_resumed",
    "process_instance_error",
    "process_instance_retried",
    "process_instance_completed",
    "process_instance_terminated",
}
TASK_EVENT_TYPES = {"task_completed", "task_failed", "task_cancelled"}
RETIRED_EVENT_TYPES = [
    "process_instance_force_run",
    "process_instance_migrated",
    "process_instance_rewound_to_task",
    "process_instance_suspended_for_error",
    "task_data_edited",
    "task_executed_manually",
    "task_skipped",
]


def test_event_enums_are_split_without_changing_persisted_values() -> None:
    assert {event.value for event in ProcessLifecycleEventType} == PROCESS_EVENT_TYPES
    assert {event.value for event in TaskEventType} == TASK_EVENT_TYPES
    assert {category.value for category in ProcessInstanceEventCategory} == {
        "process",
        "task",
    }
    for member in [*ProcessLifecycleEventType, *TaskEventType]:
        assert member == member.value

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


@pytest.mark.parametrize("event_type", sorted(PROCESS_EVENT_TYPES))
def test_process_event_types_classify_as_process(event_type: str) -> None:
    assert event_category_for_type(event_type) is ProcessInstanceEventCategory.process
    assert (
        event_category_for_type(ProcessLifecycleEventType(event_type))
        is ProcessInstanceEventCategory.process
    )


@pytest.mark.parametrize("event_type", sorted(TASK_EVENT_TYPES))
def test_task_event_types_classify_as_task(event_type: str) -> None:
    assert event_category_for_type(event_type) is ProcessInstanceEventCategory.task
    assert (
        event_category_for_type(TaskEventType(event_type))
        is ProcessInstanceEventCategory.task
    )


@pytest.mark.parametrize(
    "event_type",
    [*RETIRED_EVENT_TYPES, "Task_Completed", " task_completed", "", None, 1],
)
def test_event_classification_rejects_unknown_types(event_type: object) -> None:
    with pytest.raises(ValueError, match=re.escape(repr(event_type))):
        event_category_for_type(event_type)


@pytest.mark.parametrize("event_type", RETIRED_EVENT_TYPES)
def test_retired_event_types_are_not_enum_members(event_type: str) -> None:
    enum_cls = (
        TaskEventType if event_type.startswith("task_") else ProcessLifecycleEventType
    )
    with pytest.raises(AttributeError):
        getattr(enum_cls, event_type)
    with pytest.raises(ValidationError):
        ProcessInstanceEventModel(
            event_type=event_type,
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


ALL_PROCESS_INSTANCE_STATUSES = {
    "not_started",
    "running",
    "user_input_required",
    "waiting",
    "suspended",
    "complete",
    "error",
    "terminated",
}
TERMINAL_PROCESS_INSTANCE_STATUSES = {"complete", "error", "terminated"}
ACTIVE_PROCESS_INSTANCE_STATUSES = {
    "not_started",
    "running",
    "user_input_required",
    "waiting",
}


def test_process_instance_status_values_are_stable() -> None:
    assert {status.value for status in ProcessInstanceStatus} == (
        ALL_PROCESS_INSTANCE_STATUSES
    )
    for status in ProcessInstanceStatus:
        assert status.name == status.value


@pytest.mark.parametrize("status", sorted(ALL_PROCESS_INSTANCE_STATUSES))
def test_has_terminal_status_matches_terminal_group(status: str) -> None:
    process_instance = ProcessInstanceModel(status=status)

    assert process_instance.has_terminal_status() is (
        status in TERMINAL_PROCESS_INSTANCE_STATUSES
    )


def test_active_statuses_returns_each_active_value_once() -> None:
    active = ProcessInstanceModel.active_statuses()

    assert set(active) == ACTIVE_PROCESS_INSTANCE_STATUSES
    assert len(active) == len(ACTIVE_PROCESS_INSTANCE_STATUSES)
    assert all(type(status) is str for status in active)


def test_process_instance_status_groups_partition_all_statuses() -> None:
    terminal = {
        status
        for status in ALL_PROCESS_INSTANCE_STATUSES
        if ProcessInstanceModel(status=status).has_terminal_status()
    }
    active = set(ProcessInstanceModel.active_statuses())
    neither = {"suspended"}

    assert terminal == TERMINAL_PROCESS_INSTANCE_STATUSES
    assert terminal.isdisjoint(active)
    assert terminal.isdisjoint(neither)
    assert active.isdisjoint(neither)
    assert terminal | active | neither == ALL_PROCESS_INSTANCE_STATUSES


@pytest.mark.parametrize(
    "removed_helper",
    [
        "can_submit_task",
        "allowed_to_run",
        "can_receive_message",
        "is_immediately_runnable",
        "terminal_statuses",
        "non_terminal_statuses",
        "immediately_runnable_statuses",
    ],
)
def test_removed_process_instance_status_helpers_are_absent(
    removed_helper: str,
) -> None:
    assert not hasattr(ProcessInstanceModel, removed_helper)


def test_future_task_has_no_upsert_helper() -> None:
    assert not hasattr(FutureTaskModel, "insert_or_update")


def test_future_task_table_columns_are_stable() -> None:
    table = Base.metadata.tables["future_task"]

    assert set(table.columns.keys()) == {
        "guid",
        "m8f_tenant_id",
        "run_at",
        "queued_to_run_at",
        "completed",
        "archived_for_process_instance_status",
        "updated_at",
    }
    assert {column.name for column in table.primary_key.columns} == {"guid"}
    assert {column.name for column in table.columns if column.nullable} == {
        "queued_to_run_at"
    }
    assert {
        (fk.name, fk.parent.name, fk.target_fullname, fk.ondelete)
        for fk in table.foreign_keys
    } >= {("m8f_future_task_task_guid_fk", "guid", "task.guid", "CASCADE")}


def test_work_item_user_added_by_values_are_stable() -> None:
    from m8flow_core.models import WorkItemUserAddedBy

    assert {member.value for member in WorkItemUserAddedBy} == {
        "lane_assignment",
        "lane_owner",
        "process_initiator",
    }
    assert all(member == member.value for member in WorkItemUserAddedBy)


def test_work_item_user_added_by_column_is_stable() -> None:
    column = Base.metadata.tables["work_item_user"].columns["added_by"]

    assert column.nullable is True
    assert column.type.length == 20
