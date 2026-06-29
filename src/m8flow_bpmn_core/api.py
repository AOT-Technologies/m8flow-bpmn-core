"""Public library API for the platform/app layer.

This package is intended to be imported directly, not exposed over HTTP.

Stability: every name re-exported here is part of the public contract.
See ``doc/api.md`` for the full reference, including per-command input
fields, return types, and the error classes each operation may raise.
"""

from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

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
    ScheduleProcessInstanceRetryCommand,
    SuspendProcessInstanceCommand,
    TerminateProcessInstanceCommand,
    UpsertProcessInstanceMetadataCommand,
    execute_command,
    execute_query,
)
from m8flow_bpmn_core.application.dispatcher import _session_scope
from m8flow_bpmn_core.errors import (
    AuthorizationError,
    BpmnCoreError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from m8flow_bpmn_core.models.process_instance import ProcessInstanceStatus
from m8flow_bpmn_core.models.process_instance_event import ProcessInstanceEventType
from m8flow_bpmn_core.services.authorization import (
    PROCESS_DEFINITION_IMPORT_COMMAND,
    PROCESS_RESUME_COMMAND,
    PROCESS_RETRY_COMMAND,
    PROCESS_START_COMMAND,
    PROCESS_SUSPEND_COMMAND,
    PROCESS_TERMINATE_COMMAND,
    TASK_CLAIM_COMMAND,
    TASK_COMPLETE_COMMAND,
    AuthorizationDecision,
    AuthorizationPolicy,
    AuthorizationPolicyFactory,
    AuthorizationRequest,
    DatabaseAuthorizationPolicy,
    authorization_policy_scope,
    set_default_authorization_policy_factory,
)
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
    schedule_process_instance_retry,
    suspend_process_instance,
    terminate_process_instance,
    upsert_process_instance_metadata,
)
from m8flow_bpmn_core.services.scheduler_runtime import (
    run_due_scheduler_jobs as _run_due_scheduler_jobs,
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


def run_due_scheduler_jobs(
    session_or_connection: Session | Connection,
    *,
    now_in_seconds: int | None = None,
    limit: int = 100,
    worker_id: str = "inline",
    tenant_id: str | None = None,
) -> int:
    with _session_scope(session_or_connection) as session:
        return _run_due_scheduler_jobs(
            session,
            now_in_seconds=now_in_seconds,
            limit=limit,
            worker_id=worker_id,
            tenant_id=tenant_id,
        )

__all__ = [
    "AuthorizationError",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "AuthorizationPolicyFactory",
    "AuthorizationRequest",
    "BpmnCoreError",
    "ClaimTaskCommand",
    "CompleteTaskCommand",
    "CreateProcessInstanceCommand",
    "DatabaseAuthorizationPolicy",
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
    "PROCESS_DEFINITION_IMPORT_COMMAND",
    "PROCESS_RESUME_COMMAND",
    "PROCESS_RETRY_COMMAND",
    "PROCESS_START_COMMAND",
    "PROCESS_SUSPEND_COMMAND",
    "PROCESS_TERMINATE_COMMAND",
    "ProcessInstanceEventType",
    "ProcessInstanceStatus",
    "RecordProcessInstanceEventCommand",
    "ResumeProcessInstanceCommand",
    "RetryProcessInstanceCommand",
    "ScheduleProcessInstanceRetryCommand",
    "SuspendProcessInstanceCommand",
    "TASK_CLAIM_COMMAND",
    "TASK_COMPLETE_COMMAND",
    "TerminateProcessInstanceCommand",
    "UpsertProcessInstanceMetadataCommand",
    "ValidationError",
    "advance_process_instance_workflow",
    "authorization_policy_scope",
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
    "run_due_scheduler_jobs",
    "resume_process_instance",
    "retry_process_instance",
    "schedule_process_instance_retry",
    "set_default_authorization_policy_factory",
    "suspend_process_instance",
    "terminate_process_instance",
    "upsert_process_instance_metadata",
]
