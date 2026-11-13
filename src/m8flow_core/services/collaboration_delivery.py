from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from SpiffWorkflow.bpmn.specs.mixins.events.intermediate_event import (
    IntermediateCatchEvent,
)
from SpiffWorkflow.util.task import TaskState
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core.errors import ValidationError
from m8flow_core.models.process_instance import (
    ProcessInstanceModel,
    ProcessInstanceStatus,
)
from m8flow_core.models.process_instance_event import ProcessLifecycleEventType
from m8flow_core.models.scheduler_job import SchedulerJobModel
from m8flow_core.services.collaboration_events import CollaborationCatchDefinition
from m8flow_core.services.process_instances import record_process_instance_event
from m8flow_core.services.scheduler_jobs import delete_scheduler_job
from m8flow_core.services.tenant_users import ensure_user_belongs_to_tenant
from m8flow_core.services.workflow_runtime import (
    _advance_waiting_workflow,
    _catch_collaboration_message,
    _load_process_definition,
    _load_process_instance,
    _restore_workflow,
    _service_task_execution_context,
    _start_collaboration_participant,
)


def execute_collaboration_message_job(
    session: Session, *, job: SchedulerJobModel, occurred_at: datetime
) -> bool:
    tenant_id = job.m8f_tenant_id
    definition_id = job.bpmn_process_definition_id
    sender_id = job.process_instance_id
    if definition_id is None or sender_id is None:
        raise ValidationError(
            "Collaboration message job is missing its deployment or sender"
        )
    definition = _load_process_definition(
        session, tenant_id=tenant_id, bpmn_process_definition_id=definition_id
    )
    sender = _load_process_instance(
        session, tenant_id=tenant_id, process_instance_id=sender_id
    )
    collaboration = definition.collaboration_metadata
    payload = job.payload_json
    key = sender.collaboration_correlation_key
    version_id = sender.bpmn_version_id
    if (
        collaboration is None
        or sender.bpmn_process_definition_id != definition_id
        or version_id is None
        or payload.get("bpmn_version_id") != version_id
        or key is None
        or payload.get("correlation_key") != key
    ):
        raise ValidationError(
            "Collaboration message job does not match its sender's deployment"
        )
    route = next(
        (
            flow
            for flow in collaboration["message_flows"]
            if flow["id"] == payload.get("flow_id")
            and flow["source_ref"] == payload.get("source_ref")
            and flow["routed_message_ref"] == payload.get("message_ref")
            and flow["target_kind"] is not None
        ),
        None,
    )
    if route is None:
        raise ValidationError("Collaboration message job has no matching modeled route")

    with session.begin_nested():
        if route["target_kind"] == "start":
            ensure_user_belongs_to_tenant(
                session, tenant_id=tenant_id, user_id=sender.process_initiator_id
            )
            _start_collaboration_participant(
                session,
                process_definition=definition,
                process_initiator_id=sender.process_initiator_id,
                bpmn_process_id=route["target_process_id"],
                collaboration_correlation_key=key,
                start_event_id=route["target_ref"],
                started_at=occurred_at,
            )
        else:
            diagnostic = _deliver_to_waiting_instance(
                session,
                tenant_id=tenant_id,
                definition_id=definition_id,
                version_id=version_id,
                key=key,
                route=route,
                occurred_at=occurred_at,
            )
            if diagnostic is not None:
                job.payload_json = {**payload, "delivery_status": diagnostic}
                job.run_at = occurred_at + timedelta(seconds=5)
                job.updated_at = occurred_at
                job.locked_at = None
                job.locked_by = None
                return False
        delete_scheduler_job(session, tenant_id=tenant_id, job_key=job.job_key)
    return True


def _deliver_to_waiting_instance(
    session: Session,
    *,
    tenant_id: str,
    definition_id: int,
    version_id: int,
    key: str,
    route: dict[str, Any],
    occurred_at: datetime,
) -> str | None:
    matches = []
    candidates = session.scalars(
        select(ProcessInstanceModel).where(
            ProcessInstanceModel.m8f_tenant_id == tenant_id,
            ProcessInstanceModel.bpmn_process_definition_id == definition_id,
            ProcessInstanceModel.bpmn_version_id == version_id,
            ProcessInstanceModel.collaboration_correlation_key == key,
            ProcessInstanceModel.status.in_(
                (
                    ProcessInstanceStatus.waiting.value,
                    ProcessInstanceStatus.user_input_required.value,
                    ProcessInstanceStatus.running.value,
                )
            ),
        )
    )
    for instance in candidates:
        # Check exact equality even on databases with case-insensitive collation.
        if instance.collaboration_correlation_key != key:
            continue
        state = instance.workflow_state_json
        if state is None:
            continue
        workflow = _restore_workflow(
            state,
            service_task_context=_service_task_execution_context(
                tenant_id=tenant_id,
                process_instance_id=instance.id,
                process_definition_id=definition_id,
            ),
        )
        if workflow.spec.name != route["target_process_id"]:
            continue
        for task in workflow.get_tasks(
            state=TaskState.WAITING, spec_name=route["target_ref"]
        ):
            if (
                task.workflow is workflow
                and isinstance(task.task_spec, IntermediateCatchEvent)
                and isinstance(
                    task.task_spec.event_definition, CollaborationCatchDefinition
                )
            ):
                matches.append((instance, workflow, task))
                if len(matches) > 1:
                    return "ambiguous_recipient"
    if not matches:
        return "waiting_for_recipient"

    instance, workflow, task = matches[0]
    _catch_collaboration_message(task)
    _advance_waiting_workflow(
        session, process_instance=instance, workflow=workflow, occurred_at=occurred_at
    )
    if instance.status == ProcessInstanceStatus.complete.value:
        record_process_instance_event(
            session,
            tenant_id=tenant_id,
            process_instance_id=instance.id,
            event_type=ProcessLifecycleEventType.process_instance_completed,
            occurred_at=occurred_at,
        )
    return None
