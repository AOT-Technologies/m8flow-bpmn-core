from __future__ import annotations

from dataclasses import dataclass

from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType


@dataclass(frozen=True, slots=True)
class ClaimTaskCommand:
    tenant_id: str
    human_task_id: int
    user_id: int
    added_by: str = "manual"


@dataclass(frozen=True, slots=True)
class CompleteTaskCommand:
    tenant_id: str
    human_task_id: int
    user_id: int
    completed_at_in_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class RecordProcessInstanceEventCommand:
    tenant_id: str
    process_instance_id: int
    event_type: ProcessInstanceEventType | str
    task_guid: str | None = None
    user_id: int | None = None
    timestamp: float | None = None


@dataclass(frozen=True, slots=True)
class UpsertProcessInstanceMetadataCommand:
    tenant_id: str
    process_instance_id: int
    key: str
    value: str
    updated_at_in_seconds: int
    created_at_in_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class SuspendProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int | None = None
    suspended_at_in_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ResumeProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int | None = None
    resumed_at_in_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ErrorProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int | None = None
    errored_at_in_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class RetryProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int | None = None
    retried_at_in_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class TerminateProcessInstanceCommand:
    tenant_id: str
    process_instance_id: int
    user_id: int | None = None
    terminated_at_in_seconds: int | None = None
