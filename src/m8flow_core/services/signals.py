"""Public signal services and durable recipient workflow orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_core.errors import BpmnCoreError, NotFoundError, ValidationError
from m8flow_core.models.bpmn_process_definition import BpmnProcessDefinitionModel
from m8flow_core.models.process_instance import ProcessInstanceModel
from m8flow_core.models.signals import (
    SignalDeliveryModel,
    SignalPublicationModel,
    SignalSubscriptionModel,
)
from m8flow_core.services.signal_registry import (
    _ensure_registry,
    _inject_deliveries,
    _timestamp,
    bind_signal_runtime,
    sync_instance_signals,
)
from m8flow_core.services.signal_registry import (
    get_signal_publication as get_signal_publication,
)
from m8flow_core.services.signal_registry import (
    list_signal_deliveries as list_signal_deliveries,
)
from m8flow_core.services.signal_registry import (
    publish_signal as publish_signal,
)

# These service entrypoints intentionally remain available from this facade.
__all__ = [
    "get_signal_publication",
    "list_signal_deliveries",
    "publish_signal",
    "run_signal_deliveries",
]


def run_signal_deliveries(
    session: Session,
    *,
    tenant_id: str,
    limit: int = 100,
    now: datetime | None = None,
) -> list[SignalDeliveryModel]:
    if limit <= 0:
        raise ValidationError("Signal delivery limit must be greater than zero")
    timestamp = _timestamp(now)
    # sqlite3 legacy transaction mode does not BEGIN for SELECT. Without an
    # explicit outer transaction, releasing the first savepoint would commit it.
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if driver is not None and not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")
    _ensure_registry(session, tenant_id, timestamp)
    # Snapshot the batch, excluding suspended recipients before applying the limit.
    stmt = (
        select(SignalDeliveryModel)
        .outerjoin(
            ProcessInstanceModel,
            (ProcessInstanceModel.id == SignalDeliveryModel.process_instance_id)
            & (ProcessInstanceModel.m8f_tenant_id == SignalDeliveryModel.m8f_tenant_id),
        )
        .where(
            SignalDeliveryModel.m8f_tenant_id == tenant_id,
            SignalDeliveryModel.status == "pending",
            SignalDeliveryModel.retry_at <= timestamp,
            (ProcessInstanceModel.status.is_(None))
            | (ProcessInstanceModel.status != "suspended"),
        )
        .order_by(SignalDeliveryModel.id)
        .limit(limit)
    )
    batch = list(session.scalars(stmt))
    attempted: list[SignalDeliveryModel] = []
    seen: set[tuple[int, int | None]] = set()
    for delivery in batch:
        key = (delivery.publication_id, delivery.process_instance_id)
        # Definition starts are independent recipients even with no instance yet.
        if delivery.process_instance_id is not None and key in seen:
            continue
        seen.add(key)
        group = [delivery]
        if delivery.process_instance_id is not None:
            # All same-occurrence subscriptions must be injected before advancement.
            group = list(
                session.scalars(
                    select(SignalDeliveryModel).where(
                        SignalDeliveryModel.m8f_tenant_id == tenant_id,
                        SignalDeliveryModel.publication_id == delivery.publication_id,
                        SignalDeliveryModel.process_instance_id
                        == delivery.process_instance_id,
                        SignalDeliveryModel.status == "pending",
                    )
                )
            )
        try:
            with session.begin_nested():
                _execute_delivery_group(session, tenant_id, group, timestamp)
                session.flush()
        except Exception as exc:
            # Savepoint rollback leaves consumption and workflow changes atomic.
            for failed in group:
                failed.attempts += 1
                failed.last_error = str(exc)[:2000]
                failed.retry_at = timestamp + timedelta(seconds=30)
        attempted.extend(group)
    session.flush()
    return attempted


def _execute_delivery_group(
    session: Session,
    tenant_id: str,
    group: list[SignalDeliveryModel],
    timestamp: datetime,
) -> None:
    from m8flow_core.services import workflow_runtime as runtime

    instance: ProcessInstanceModel | None

    first = group[0]
    publication = session.get(SignalPublicationModel, first.publication_id)
    subscription = session.get(SignalSubscriptionModel, first.subscription_id)
    if publication is None or subscription is None:
        raise BpmnCoreError("Signal delivery references missing registry records")
    if subscription.kind == "start":
        instance, workflow = _start_signal_instance(
            session,
            subscription,
            timestamp,
        )
        first.process_instance_id = instance.id
        starts = workflow.get_tasks(spec_name=subscription.task_spec_name)
        start = next(
            t for t in starts if t.task_spec in workflow.spec.bpmn_start_events
        )
        from SpiffWorkflow.bpmn.util.event import BpmnEvent

        start.task_spec.catch(
            start,
            BpmnEvent(
                start.task_spec.event_definition,
                payload=publication.payload_json,
            ),
        )
        start.task_spec._update(start)
        first.status = "delivered"
        first.attempts += 1
        first.delivered_at = timestamp
    else:
        instance = session.scalar(
            select(ProcessInstanceModel)
            .where(
                ProcessInstanceModel.m8f_tenant_id == tenant_id,
                ProcessInstanceModel.id == first.process_instance_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if instance is None or instance.has_terminal_status():
            for delivery in group:
                delivery.status = "skipped"
                delivery.last_error = "Recipient instance has ended"
                delivery.delivered_at = timestamp
                delivery.attempts += 1
            return
        if instance.status == "suspended":
            return
        state = instance.workflow_state_json
        if state is None:
            raise ValidationError("Signal recipient has no persisted workflow")
        workflow = runtime._restore_workflow(
            state,
            service_task_context=runtime._service_task_execution_context(
                tenant_id=tenant_id,
                process_instance_id=instance.id,
                process_definition_id=instance.bpmn_process_definition_id,
            ),
        )
        _inject_deliveries(session, instance, workflow, publication, group, timestamp)
    bind_signal_runtime(session, instance, workflow, timestamp)
    try:
        runtime._run_workflow_with_service_task_failure_handling(
            session,
            tenant_id=tenant_id,
            process_instance=instance,
            workflow=workflow,
            occurred_at=timestamp,
        )
    except Exception as exc:
        # The event has been consumed. Preserve the failed workflow for normal retry.
        if instance.status != "error":
            runtime._persist_workflow_state(
                session, instance, workflow, occurred_at=timestamp
            )
            from m8flow_core.services.process_instances import error_process_instance

            error_process_instance(
                session,
                tenant_id=tenant_id,
                process_instance_id=instance.id,
                errored_at=timestamp,
            )
        for delivery in group:
            delivery.last_error = str(exc)[:2000]
        return
    runtime._persist_workflow_state(session, instance, workflow, occurred_at=timestamp)
    runtime._sync_inactive_work_items(
        session,
        process_instance=instance,
        workflow=workflow,
        occurred_at=timestamp,
    )
    runtime._materialize_ready_manual_tasks(
        session,
        process_instance=instance,
        workflow=workflow,
        occurred_at=timestamp,
    )
    runtime._update_process_instance_status_from_workflow(
        instance,
        workflow,
        occurred_at=timestamp,
    )
    sync_instance_signals(session, instance, workflow, timestamp)


def _start_signal_instance(
    session: Session,
    subscription: SignalSubscriptionModel,
    timestamp: datetime,
) -> tuple[ProcessInstanceModel, Any]:
    from m8flow_core.models.user import UserModel
    from m8flow_core.services import workflow_runtime as runtime

    definition = session.scalar(
        select(BpmnProcessDefinitionModel).where(
            BpmnProcessDefinitionModel.m8f_tenant_id == subscription.m8f_tenant_id,
            BpmnProcessDefinitionModel.id == subscription.definition_id,
        )
    )
    if definition is None:
        raise NotFoundError("Signal start definition no longer exists")
    source = definition.source_bpmn_xml
    if source is None:
        raise ValidationError("Signal start definition has no BPMN source")
    service = f"m8flow/realms/{subscription.m8f_tenant_id}"
    user = session.scalar(
        select(UserModel).where(
            UserModel.service == service,
            UserModel.service_id == "__m8f_signal_start__",
        )
    )
    if user is None:
        user = UserModel(
            username=f"__m8f_signal_start__:{subscription.m8f_tenant_id}",
            service=service,
            service_id="__m8f_signal_start__",
            display_name="Signal start system user",
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(user)
        session.flush()
    instance, process_id = runtime._prepare_process_instance_from_definition_in_session(
        session,
        tenant_id=subscription.m8f_tenant_id,
        process_definition=definition,
        process_model_identifier=definition.process_model_identifier,
        process_initiator_id=user.id,
        submission_metadata=None,
        summary=None,
        process_version=1,
        started_at=timestamp,
        bpmn_process_id=definition.bpmn_identifier,
    )
    workflow = runtime._build_workflow(
        bpmn_xml=source,
        dmn_xml=definition.source_dmn_xml,
        bpmn_process_id=process_id,
        service_task_context=runtime._service_task_execution_context(
            tenant_id=subscription.m8f_tenant_id,
            process_instance_id=instance.id,
            process_definition_id=definition.id,
        ),
    )
    runtime._seed_runtime_definitions(
        session,
        tenant_id=subscription.m8f_tenant_id,
        process_instance=instance,
        workflow=workflow,
        occurred_at=timestamp,
    )
    bind_signal_runtime(session, instance, workflow, timestamp)
    # Activate only the selected top-level start; unrelated starts must not run.
    root = workflow.task_tree
    root.run()
    for task in list(root.children):
        if task.task_spec in workflow.spec.bpmn_start_events and (
            task.task_spec.name != subscription.task_spec_name
        ):
            task.cancel()
    instance.started_at = timestamp
    from m8flow_core.services.process_instances import record_process_instance_event

    record_process_instance_event(
        session,
        tenant_id=subscription.m8f_tenant_id,
        process_instance_id=instance.id,
        event_type="process_instance_created",
        occurred_at=timestamp,
    )
    return instance, workflow
