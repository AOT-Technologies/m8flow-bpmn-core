from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from m8flow_bpmn_core.application.commands import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    CreateProcessInstanceCommand,
    ErrorProcessInstanceCommand,
    GetPendingTasksCommand,
    GetProcessInstanceCommand,
    GetProcessInstanceEventsCommand,
    GetProcessInstanceMetadataCommand,
    ImportBpmnProcessDefinitionCommand,
    InitializeProcessInstanceFromDefinitionCommand,
    InitializeProcessInstanceWorkflowCommand,
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
from m8flow_bpmn_core.services.process_definitions import (
    import_bpmn_process_definition,
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
    initialize_process_instance_from_definition,
    initialize_process_instance_workflow,
)


@contextmanager
def _session_scope(
    session_or_connection: Session | Connection,
) -> Iterator[Session]:
    if isinstance(session_or_connection, Session):
        yield session_or_connection
        return

    session = Session(
        bind=session_or_connection,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()


def execute_command(
    session_or_connection: Session | Connection,
    command: object,
) -> object:
    with _session_scope(session_or_connection) as session:
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
        if isinstance(command, CreateProcessInstanceCommand):
            return create_process_instance(
                session,
                tenant_id=command.tenant_id,
                process_model_identifier=command.process_model_identifier,
                process_model_display_name=command.process_model_display_name,
                process_initiator_id=command.process_initiator_id,
                bpmn_process_definition_id=command.bpmn_process_definition_id,
                bpmn_process_id=command.bpmn_process_id,
                summary=command.summary,
                process_version=command.process_version,
                created_at_in_seconds=command.created_at_in_seconds,
                updated_at_in_seconds=command.updated_at_in_seconds,
            )
        if isinstance(command, GetProcessInstanceCommand):
            return get_process_instance(
                session,
                tenant_id=command.tenant_id,
                process_instance_id=command.process_instance_id,
            )
        if isinstance(command, GetPendingTasksCommand):
            return get_pending_tasks(
                session,
                tenant_id=command.tenant_id,
                user_id=command.user_id,
            )
        if isinstance(command, GetProcessInstanceEventsCommand):
            return get_process_instance_events(
                session,
                tenant_id=command.tenant_id,
                process_instance_id=command.process_instance_id,
            )
        if isinstance(command, GetProcessInstanceMetadataCommand):
            return get_process_instance_metadata(
                session,
                tenant_id=command.tenant_id,
                process_instance_id=command.process_instance_id,
            )
        if isinstance(command, ImportBpmnProcessDefinitionCommand):
            return import_bpmn_process_definition(
                session,
                tenant_id=command.tenant_id,
                bpmn_identifier=command.bpmn_identifier,
                source_bpmn_xml=command.source_bpmn_xml,
                source_dmn_xml=command.source_dmn_xml,
                bpmn_name=command.bpmn_name,
                properties_json=command.properties_json,
                bpmn_version_control_type=command.bpmn_version_control_type,
                bpmn_version_control_identifier=(
                    command.bpmn_version_control_identifier
                ),
                single_process_hash=command.single_process_hash,
                full_process_model_hash=command.full_process_model_hash,
                created_at_in_seconds=command.created_at_in_seconds,
                updated_at_in_seconds=command.updated_at_in_seconds,
            )
        if isinstance(command, InitializeProcessInstanceFromDefinitionCommand):
            return initialize_process_instance_from_definition(
                session,
                tenant_id=command.tenant_id,
                bpmn_process_definition_id=command.bpmn_process_definition_id,
                process_initiator_id=command.process_initiator_id,
                submission_metadata=command.submission_metadata,
                summary=command.summary,
                process_version=command.process_version,
                started_at_in_seconds=command.started_at_in_seconds,
                bpmn_process_id=command.bpmn_process_id,
            )
        if isinstance(command, InitializeProcessInstanceWorkflowCommand):
            return initialize_process_instance_workflow(
                session,
                tenant_id=command.tenant_id,
                process_instance_id=command.process_instance_id,
                bpmn_xml=command.bpmn_xml,
                dmn_xml=command.dmn_xml,
                bpmn_process_id=command.bpmn_process_id,
                started_at_in_seconds=command.started_at_in_seconds,
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


def execute_query(
    session_or_connection: Session | Connection,
    query: object,
) -> object:
    with _session_scope(session_or_connection) as session:
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
