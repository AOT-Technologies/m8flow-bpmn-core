from __future__ import annotations

from sqlalchemy.orm import Session

from m8flow_bpmn_core.application.commands import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    ErrorProcessInstanceCommand,
    RecordProcessInstanceEventCommand,
    ResumeProcessInstanceCommand,
    RetryProcessInstanceCommand,
    SuspendProcessInstanceCommand,
    TerminateProcessInstanceCommand,
    UpsertProcessInstanceMetadataCommand,
)
from m8flow_bpmn_core.application.queries import (
    GetPendingTasksQuery,
    GetProcessInstanceEventsQuery,
    GetProcessInstanceMetadataQuery,
    GetProcessInstanceQuery,
    ListErrorProcessInstancesQuery,
    ListProcessInstancesQuery,
    ListSuspendedProcessInstancesQuery,
    ListTerminatedProcessInstancesQuery,
)
from m8flow_bpmn_core.services.process_instances import (
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


def execute_command(session: Session, command: object) -> object:
    if isinstance(command, ClaimTaskCommand):
        return claim_task(
            session,
            tenant_id=command.tenant_id,
            human_task_id=command.human_task_id,
            user_id=command.user_id,
            added_by=command.added_by,
        )
    if isinstance(command, CompleteTaskCommand):
        return complete_task(
            session,
            tenant_id=command.tenant_id,
            human_task_id=command.human_task_id,
            user_id=command.user_id,
            completed_at_in_seconds=command.completed_at_in_seconds,
        )
    if isinstance(command, RecordProcessInstanceEventCommand):
        return record_process_instance_event(
            session,
            tenant_id=command.tenant_id,
            process_instance_id=command.process_instance_id,
            event_type=command.event_type,
            task_guid=command.task_guid,
            user_id=command.user_id,
            timestamp=command.timestamp,
        )
    if isinstance(command, UpsertProcessInstanceMetadataCommand):
        return upsert_process_instance_metadata(
            session,
            tenant_id=command.tenant_id,
            process_instance_id=command.process_instance_id,
            key=command.key,
            value=command.value,
            updated_at_in_seconds=command.updated_at_in_seconds,
            created_at_in_seconds=command.created_at_in_seconds,
        )
    if isinstance(command, SuspendProcessInstanceCommand):
        return suspend_process_instance(
            session,
            tenant_id=command.tenant_id,
            process_instance_id=command.process_instance_id,
            user_id=command.user_id,
            suspended_at_in_seconds=command.suspended_at_in_seconds,
        )
    if isinstance(command, ErrorProcessInstanceCommand):
        return error_process_instance(
            session,
            tenant_id=command.tenant_id,
            process_instance_id=command.process_instance_id,
            user_id=command.user_id,
            errored_at_in_seconds=command.errored_at_in_seconds,
        )
    if isinstance(command, ResumeProcessInstanceCommand):
        return resume_process_instance(
            session,
            tenant_id=command.tenant_id,
            process_instance_id=command.process_instance_id,
            user_id=command.user_id,
            resumed_at_in_seconds=command.resumed_at_in_seconds,
        )
    if isinstance(command, RetryProcessInstanceCommand):
        return retry_process_instance(
            session,
            tenant_id=command.tenant_id,
            process_instance_id=command.process_instance_id,
            user_id=command.user_id,
            retried_at_in_seconds=command.retried_at_in_seconds,
        )
    if isinstance(command, TerminateProcessInstanceCommand):
        return terminate_process_instance(
            session,
            tenant_id=command.tenant_id,
            process_instance_id=command.process_instance_id,
            user_id=command.user_id,
            terminated_at_in_seconds=command.terminated_at_in_seconds,
        )
    raise TypeError(f"Unsupported command type: {type(command)!r}")


def execute_query(session: Session, query: object) -> object:
    if isinstance(query, GetPendingTasksQuery):
        return get_pending_tasks(
            session,
            tenant_id=query.tenant_id,
            user_id=query.user_id,
        )
    if isinstance(query, GetProcessInstanceEventsQuery):
        return get_process_instance_events(
            session,
            tenant_id=query.tenant_id,
            process_instance_id=query.process_instance_id,
        )
    if isinstance(query, GetProcessInstanceMetadataQuery):
        return get_process_instance_metadata(
            session,
            tenant_id=query.tenant_id,
            process_instance_id=query.process_instance_id,
        )
    if isinstance(query, GetProcessInstanceQuery):
        return get_process_instance(
            session,
            tenant_id=query.tenant_id,
            process_instance_id=query.process_instance_id,
        )
    if isinstance(query, ListErrorProcessInstancesQuery):
        return list_error_process_instances(session, tenant_id=query.tenant_id)
    if isinstance(query, ListProcessInstancesQuery):
        return list_process_instances(
            session,
            tenant_id=query.tenant_id,
            status=query.status,
        )
    if isinstance(query, ListSuspendedProcessInstancesQuery):
        return list_suspended_process_instances(session, tenant_id=query.tenant_id)
    if isinstance(query, ListTerminatedProcessInstancesQuery):
        return list_terminated_process_instances(session, tenant_id=query.tenant_id)
    raise TypeError(f"Unsupported query type: {type(query)!r}")
