"""Public library API for the platform/app layer.

This package is intended to be imported directly, not exposed over HTTP.

Stability: every name re-exported here is part of the public contract.
See ``doc/api.md`` for the full reference, including per-command input
fields, return types, and the error classes each operation may raise.
"""

from __future__ import annotations

from m8flow_bpmn_core.application import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    CreateProcessInstanceCommand,
    ErrorProcessInstanceCommand,
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    ImportBpmnProcessDefinitionCommand,
    InitializeProcessInstanceFromDefinitionCommand,
    InitializeProcessInstanceWorkflowCommand,
    ListErrorProcessInstancesQuery,
    ListProcessInstancesQuery,
    ListSuspendedProcessInstancesQuery,
    ListTerminatedProcessInstancesQuery,
    RecordProcessInstanceEventCommand,
    ResumeProcessInstanceCommand,
    RetryProcessInstanceCommand,
    SuspendProcessInstanceCommand,
    TerminateProcessInstanceCommand,
    UpsertProcessInstanceMetadataCommand,
    execute_command,
    execute_query,
)
from m8flow_bpmn_core.errors import (
    AuthorizationError,
    BpmnCoreError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from m8flow_bpmn_core.models.process_instance import ProcessInstanceStatus
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.services.process_instances import (
    create_process_instance,
    error_process_instance,
    get_process_instance,
    get_process_instance_events,
    get_process_instance_metadata,
    list_error_process_instances,
    list_process_instances,
    list_suspended_process_instances,
    list_terminated_process_instances,
    record_process_instance_event,
    resume_process_instance,
    retry_process_instance,
    suspend_process_instance,
    terminate_process_instance,
    upsert_process_instance_metadata,
)
from m8flow_bpmn_core.services.tasks import (
    claim_task,
    complete_task,
    get_pending_tasks,
)
from m8flow_bpmn_core.services.workflow_runtime import (
    advance_process_instance_workflow,
    resolve_lane_assignment_id,
)

__all__ = [
    "AuthorizationError",
    "BpmnCoreError",
    "ClaimTaskCommand",
    "CompleteTaskCommand",
    "CreateProcessInstanceCommand",
    "ErrorProcessInstanceCommand",
    "GetPendingTasksQuery",
    "GetProcessInstanceEventsQuery",
    "GetProcessInstanceMetadataQuery",
    "GetProcessInstanceQuery",
    "ImportBpmnProcessDefinitionCommand",
    "InitializeProcessInstanceFromDefinitionCommand",
    "InitializeProcessInstanceWorkflowCommand",
    "InvalidStateError",
    "ListErrorProcessInstancesQuery",
    "ListProcessInstancesQuery",
    "ListSuspendedProcessInstancesQuery",
    "ListTerminatedProcessInstancesQuery",
    "NotFoundError",
    "ProcessInstanceEventType",
    "ProcessInstanceStatus",
    "RecordProcessInstanceEventCommand",
    "ResumeProcessInstanceCommand",
    "RetryProcessInstanceCommand",
    "SuspendProcessInstanceCommand",
    "TerminateProcessInstanceCommand",
    "UpsertProcessInstanceMetadataCommand",
    "ValidationError",
    "advance_process_instance_workflow",
    "claim_task",
    "complete_task",
    "create_process_instance",
    "error_process_instance",
    "execute_command",
    "execute_query",
    "get_pending_tasks",
    "get_process_instance",
    "get_process_instance_events",
    "get_process_instance_metadata",
    "list_error_process_instances",
    "list_process_instances",
    "list_suspended_process_instances",
    "list_terminated_process_instances",
    "record_process_instance_event",
    "resolve_lane_assignment_id",
    "resume_process_instance",
    "retry_process_instance",
    "suspend_process_instance",
    "terminate_process_instance",
    "upsert_process_instance_metadata",
]
