from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from m8flow_bpmn_core.models.process_instance_event import (
    ProcessLifecycleEventType,
    TaskEventType,
)


@dataclass(frozen=True, slots=True)
class ClaimTaskCommand:
    tenant_id: str
    work_item_id: int
    user_id: int
    added_by: str = "manual"


@dataclass(frozen=True, slots=True)
class CompleteTaskCommand:
    tenant_id: str
    work_item_id: int
    user_id: int
    completed_at: datetime | None = None
    task_payload: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RecordProcessInstanceEventCommand:
    tenant_id: str
    process_instance_id: int
    event_type: (
        ProcessLifecycleEventType | TaskEventType | str
    )
    task_guid: str | None = None
    user_id: int | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CreateProcessInstanceCommand:
    tenant_id: str
    process_model_identifier: str
    process_model_display_name: str
    process_initiator_id: int
    bpmn_process_definition_id: int
    bpmn_process_id: int
    summary: str | None = None
    process_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ImportBpmnProcessDefinitionCommand:
    tenant_id: str
    bpmn_identifier: str
    user_id: int
    source_bpmn_xml: str | bytes
    source_dmn_xml: str | bytes | None = None
    bpmn_name: str | None = None
    properties_json: dict[str, Any] | None = None
    bpmn_version_control_type: str | None = None
    bpmn_version_control_identifier: str | None = None
    process_xml_digest: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class InitializeProcessInstanceFromDefinitionCommand:
    tenant_id: str
    bpmn_process_definition_id: int
    process_initiator_id: int
    submission_metadata: dict[str, str] | None = None
    summary: str | None = None
    process_version: int = 1
    started_at: datetime | None = None
    bpmn_process_id: str | None = None


@dataclass(frozen=True, slots=True)
class InitializeProcessInstanceWorkflowCommand:
    tenant_id: str
    process_instance_id: int
    bpmn_xml: str | bytes
    bpmn_process_id: str | None = None
    started_at: datetime | None = None
    dmn_xml: str | bytes | None = None


@dataclass(frozen=True, slots=True)
class UpsertProcessInstanceMetadataCommand:
    tenant_id: str
    process_instance_id: int
    key: str
    value: str
    updated_at: datetime
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SuspendProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int
    suspended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResumeProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int
    resumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ErrorProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int | None = None
    errored_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RetryProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int
    retried_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ScheduleProcessInstanceRetryCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int
    retry_at: datetime
    scheduled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TerminateProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int
    terminated_at: datetime | None = None
