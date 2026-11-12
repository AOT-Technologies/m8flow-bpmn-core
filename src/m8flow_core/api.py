"""Public library API for the platform/app layer.

This package is intended to be imported directly, not exposed over HTTP.

Stability: every name re-exported here is part of the public contract.
See ``docs/reference/api.md`` for the full reference, including per-command input
fields, return types, and the error classes each operation may raise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from m8flow_core.application import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    CreateProcessInstanceCommand,
    ErrorProcessInstanceCommand,
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    GetSignalPublicationQuery,
    ImportBpmnProcessDefinitionCommand,
    InitializeProcessInstanceFromDefinitionCommand,
    InitializeProcessInstanceWorkflowCommand,
    ListErrorProcessInstancesQuery,
    ListProcessInstancesQuery,
    ListSignalDeliveriesQuery,
    ListSuspendedProcessInstancesQuery,
    ListTerminatedProcessInstancesQuery,
    PublishSignalCommand,
    RecordProcessInstanceEventCommand,
    ResumeProcessInstanceCommand,
    RetryProcessInstanceCommand,
    RunSignalDeliveriesCommand,
    ScheduleProcessInstanceRetryCommand,
    SuspendProcessInstanceCommand,
    TerminateProcessInstanceCommand,
    UpsertProcessInstanceMetadataCommand,
    execute_command,
    execute_query,
)
from m8flow_core.application.dispatcher import _session_scope
from m8flow_core.errors import (
    AuthorizationError,
    BpmnCoreError,
    CallActivityResolutionError,
    InvalidStateError,
    NotFoundError,
    ServiceTaskExecutionError,
    ValidationError,
)
from m8flow_core.models.process_instance import ProcessInstanceStatus
from m8flow_core.models.process_instance_event import (
    ProcessInstanceEventCategory,
    ProcessLifecycleEventType,
    TaskEventType,
)
from m8flow_core.models.signals import SignalDeliveryModel, SignalPublicationModel
from m8flow_core.services.authorization import (
    PROCESS_DEFINITION_IMPORT_COMMAND,
    PROCESS_RESUME_COMMAND,
    PROCESS_RETRY_COMMAND,
    PROCESS_START_COMMAND,
    PROCESS_SUSPEND_COMMAND,
    PROCESS_TERMINATE_COMMAND,
    SIGNAL_PUBLISH_COMMAND,
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
from m8flow_core.services.call_activities import (
    CallActivityContext,
    CallActivityDefinition,
    CallActivityResolver,
)
from m8flow_core.services.connector_proxy_service_tasks import (
    ConnectorProxyServiceTaskConnector,
    build_connector_proxy_service_task_connectors,
    build_connector_proxy_service_task_registry,
    fetch_connector_proxy_command_definitions,
)
from m8flow_core.services.lane_assignment import resolve_lane_assignment_id
from m8flow_core.services.process_instance_reactivation import (
    resume_process_instance,
    retry_process_instance,
)
from m8flow_core.services.process_instances import (
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
    schedule_process_instance_retry,
    suspend_process_instance,
    terminate_process_instance,
    upsert_process_instance_metadata,
)
from m8flow_core.services.scheduler_runtime import (
    run_due_scheduler_jobs as _run_due_scheduler_jobs,
)
from m8flow_core.services.service_tasks import (
    ServiceTaskCommandDefinition,
    ServiceTaskConnector,
    ServiceTaskContext,
    ServiceTaskParameterDefinition,
    ServiceTaskRegistry,
    ServiceTaskRegistryFactory,
    ServiceTaskRequest,
    ServiceTaskResult,
    build_service_task_operation_id,
    service_task_registry_scope,
    set_default_service_task_registry_factory,
    split_service_task_operation_id,
)
from m8flow_core.services.signals import (
    get_signal_publication as _get_signal_publication,
)
from m8flow_core.services.signals import (
    list_signal_deliveries as _list_signal_deliveries,
)
from m8flow_core.services.signals import (
    publish_signal as _publish_signal,
)
from m8flow_core.services.signals import (
    run_signal_deliveries as _run_signal_deliveries,
)
from m8flow_core.services.tasks import (
    assign_pending_tasks_for_user,
    claim_task,
    complete_task,
    get_pending_tasks,
)
from m8flow_core.services.workflow_runtime import (
    advance_process_instance_workflow,
)


def publish_signal(
    session_or_connection: Session | Connection, *, tenant_id: str,
    signal_name: str, user_id: int, occurrence_id: str, payload: Any = None,
    published_at: datetime | None = None,
) -> SignalPublicationModel:
    with _session_scope(session_or_connection) as session:
        return _publish_signal(
            session, tenant_id=tenant_id, signal_name=signal_name, user_id=user_id,
            occurrence_id=occurrence_id, payload=payload, published_at=published_at,
        )


def run_signal_deliveries(
    session_or_connection: Session | Connection, *, tenant_id: str,
    limit: int = 100, now: datetime | None = None,
) -> list[SignalDeliveryModel]:
    with _session_scope(session_or_connection) as session:
        return _run_signal_deliveries(
            session, tenant_id=tenant_id, limit=limit, now=now,
        )


def get_signal_publication(
    session_or_connection: Session | Connection, *,
    tenant_id: str, occurrence_id: str,
) -> SignalPublicationModel:
    with _session_scope(session_or_connection) as session:
        return _get_signal_publication(
            session, tenant_id=tenant_id, occurrence_id=occurrence_id,
        )


def list_signal_deliveries(
    session_or_connection: Session | Connection, *,
    tenant_id: str, occurrence_id: str,
) -> list[SignalDeliveryModel]:
    with _session_scope(session_or_connection) as session:
        return _list_signal_deliveries(
            session, tenant_id=tenant_id, occurrence_id=occurrence_id,
        )


def run_due_scheduler_jobs(
    session_or_connection: Session | Connection,
    *,
    now: datetime | None = None,
    limit: int = 100,
    worker_id: str = "inline",
    tenant_id: str | None = None,
) -> int:
    with _session_scope(session_or_connection) as session:
        return _run_due_scheduler_jobs(
            session,
            now=now,
            limit=limit,
            worker_id=worker_id,
            tenant_id=tenant_id,
        )

__all__ = [
    "PublishSignalCommand", "RunSignalDeliveriesCommand",
    "GetSignalPublicationQuery", "ListSignalDeliveriesQuery",
    "SIGNAL_PUBLISH_COMMAND", "publish_signal", "run_signal_deliveries",
    "get_signal_publication", "list_signal_deliveries",
    "AuthorizationError",
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "AuthorizationPolicyFactory",
    "AuthorizationRequest",
    "BpmnCoreError",
    "CallActivityContext",
    "CallActivityDefinition",
    "CallActivityResolutionError",
    "CallActivityResolver",
    "ClaimTaskCommand",
    "CompleteTaskCommand",
    "ConnectorProxyServiceTaskConnector",
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
    "ProcessInstanceEventCategory",
    "ProcessLifecycleEventType",
    "ProcessInstanceStatus",
    "RecordProcessInstanceEventCommand",
    "ResumeProcessInstanceCommand",
    "RetryProcessInstanceCommand",
    "ScheduleProcessInstanceRetryCommand",
    "ServiceTaskCommandDefinition",
    "ServiceTaskConnector",
    "ServiceTaskContext",
    "ServiceTaskExecutionError",
    "ServiceTaskParameterDefinition",
    "ServiceTaskRegistry",
    "ServiceTaskRegistryFactory",
    "ServiceTaskRequest",
    "ServiceTaskResult",
    "SuspendProcessInstanceCommand",
    "TASK_CLAIM_COMMAND",
    "TASK_COMPLETE_COMMAND",
    "TaskEventType",
    "TerminateProcessInstanceCommand",
    "UpsertProcessInstanceMetadataCommand",
    "ValidationError",
    "advance_process_instance_workflow",
    "assign_pending_tasks_for_user",
    "authorization_policy_scope",
    "build_connector_proxy_service_task_connectors",
    "build_connector_proxy_service_task_registry",
    "build_service_task_operation_id",
    "claim_task",
    "complete_task",
    "create_process_instance",
    "error_process_instance",
    "execute_command",
    "execute_query",
    "fetch_connector_proxy_command_definitions",
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
    "service_task_registry_scope",
    "set_default_authorization_policy_factory",
    "set_default_service_task_registry_factory",
    "split_service_task_operation_id",
    "suspend_process_instance",
    "terminate_process_instance",
    "upsert_process_instance_metadata",
]
