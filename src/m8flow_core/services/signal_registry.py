"""Signal persistence and subscriptions without orchestration dependencies.

The caller owns the transaction. Run one delivery loop per tenant.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from m8flow_core.errors import NotFoundError, ValidationError
from m8flow_core.models.bpmn_process_definition import BpmnProcessDefinitionModel
from m8flow_core.models.process_instance import ProcessInstanceModel
from m8flow_core.models.signals import (
    SignalDeliveryModel,
    SignalPublicationModel,
    SignalRegistryStateModel,
    SignalSubscriptionModel,
)
from m8flow_core.services.signal_runtime import (
    RuntimeSignalDefinition,
    deliver_to_subscription,
    event_starts,
    interrupting_starts,
    scope_key,
    scopes,
)
from m8flow_core.services.tenant_users import ensure_user_belongs_to_tenant


def _timestamp(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Signal timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise ValidationError(
            f"{label} must be a non-blank string of at most 255 characters"
        )
    return value


def get_signal_publication(
    session: Session,
    *,
    tenant_id: str,
    occurrence_id: str,
) -> SignalPublicationModel:
    row = session.scalar(
        select(SignalPublicationModel).where(
            SignalPublicationModel.m8f_tenant_id == tenant_id,
            SignalPublicationModel.occurrence_id == occurrence_id,
        )
    )
    if row is None:
        raise NotFoundError(f"Signal occurrence {occurrence_id!r} was not found")
    return row


def list_signal_deliveries(
    session: Session,
    *,
    tenant_id: str,
    occurrence_id: str,
) -> list[SignalDeliveryModel]:
    publication = get_signal_publication(
        session,
        tenant_id=tenant_id,
        occurrence_id=occurrence_id,
    )
    return list(
        session.scalars(
            select(SignalDeliveryModel)
            .where(
                SignalDeliveryModel.m8f_tenant_id == tenant_id,
                SignalDeliveryModel.publication_id == publication.id,
            )
            .order_by(SignalDeliveryModel.id)
        )
    )


def _subscriptions(
    session: Session,
    tenant_id: str,
    *,
    process_instance_id: int | None = None,
    model_identifier: str | None = None,
) -> list[SignalSubscriptionModel]:
    statement = select(SignalSubscriptionModel).where(
        SignalSubscriptionModel.m8f_tenant_id == tenant_id,
    )
    if process_instance_id is not None:
        statement = statement.where(
            SignalSubscriptionModel.process_instance_id == process_instance_id,
        )
    if model_identifier is not None:
        statement = statement.where(
            SignalSubscriptionModel.kind == "start",
            SignalSubscriptionModel.process_model_identifier == model_identifier,
        )
    return list(session.scalars(statement))


def _upsert_subscription(
    session: Session,
    tenant_id: str,
    key: str,
    timestamp: datetime,
    **values: Any,
) -> SignalSubscriptionModel:
    if len(key) > 255:
        suffix = (
            ":" + key.rsplit(":", 1)[-1]
            if values["kind"] in ("catch", "boundary") and not values["repeatable"]
            else ""
        )
        key = "sha256:" + hashlib.sha256(key.encode()).hexdigest() + suffix
    row = session.scalar(
        select(SignalSubscriptionModel).where(
            SignalSubscriptionModel.m8f_tenant_id == tenant_id,
            SignalSubscriptionModel.subscription_key == key,
        )
    )
    if row is None:
        row = SignalSubscriptionModel(
            m8f_tenant_id=tenant_id,
            subscription_key=key,
            created_at=timestamp,
            active=True,
            reserved=False,
            **values,
        )
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
        row.active = True
    return row


def validate_signal_source(
    source: str,
    dmn_xml: str | None = None,
    process_id: str | None = None,
) -> Any:
    """Validate signal references and names before definition rows are written."""
    from defusedxml import ElementTree
    from SpiffWorkflow.bpmn.parser.BpmnParser import full_tag

    from m8flow_core.services.workflow_engine import _build_workflow

    try:
        root = ElementTree.fromstring(source)
    except Exception as exc:
        raise ValidationError(f"Signal BPMN source is invalid: {exc}") from exc
    if next(root.iter(full_tag("signalEventDefinition")), None) is None:
        return None
    try:
        workflow = _build_workflow(
            bpmn_xml=source,
            dmn_xml=dmn_xml,
            bpmn_process_id=process_id,
        )
    except Exception as exc:
        raise ValidationError(f"Signal BPMN definition is invalid: {exc}") from exc
    for spec in [workflow.spec, *workflow.subprocess_specs.values()]:
        for task_spec in spec.task_specs.values():
            event = getattr(task_spec, "event_definition", None)
            if isinstance(event, RuntimeSignalDefinition):
                _identifier(event.name, "Signal name")
    return workflow


def sync_definition_signals(
    session: Session,
    definition: BpmnProcessDefinitionModel,
    timestamp: datetime,
) -> None:
    source = definition.source_bpmn_xml
    if source is None:
        return

    for row in _subscriptions(
        session,
        definition.m8f_tenant_id,
        model_identifier=definition.process_model_identifier,
    ):
        if row.kind == "start" and (
            row.process_model_identifier == definition.process_model_identifier
        ):
            row.active = False
    workflow = validate_signal_source(
        source,
        definition.source_dmn_xml,
        definition.bpmn_identifier,
    )
    if workflow is None:
        session.flush()
        return
    for start in workflow.spec.bpmn_start_events:
        event = start.event_definition
        if not isinstance(event, RuntimeSignalDefinition):
            continue
        _identifier(event.name, "Signal name")
        _upsert_subscription(
            session,
            definition.m8f_tenant_id,
            f"start:{definition.id}:{start.name}",
            timestamp,
            signal_name=event.name,
            kind="start",
            definition_id=definition.id,
            process_instance_id=None,
            scope_id=None,
            task_guid=None,
            task_spec_name=start.name,
            process_model_identifier=definition.process_model_identifier,
            repeatable=True,
        )
    session.flush()


def sync_instance_signals(
    session: Session,
    instance: ProcessInstanceModel,
    workflow: Any,
    timestamp: datetime,
) -> None:
    from SpiffWorkflow.bpmn.specs.mixins.events.intermediate_event import BoundaryEvent
    from SpiffWorkflow.util.task import TaskState

    existing = [
        s
        for s in _subscriptions(
            session, instance.m8f_tenant_id, process_instance_id=instance.id
        )
        if s.process_instance_id == instance.id
    ]
    for row in existing:
        row.active = False
    if instance.has_terminal_status() or workflow.completed:
        return
    interrupts = interrupting_starts(instance.bpmn_process_definition.source_bpmn_xml)
    for scope in scopes(workflow):
        if scope.completed:
            continue
        for task in list(workflow.event_manager.tasks.values()):
            event = getattr(task.task_spec, "event_definition", None)
            if task.workflow is not scope or task.state != TaskState.WAITING:
                continue
            if not isinstance(event, RuntimeSignalDefinition):
                continue
            # Top-level signal starts instantiate via definition subscriptions.
            if scope is workflow and task.task_spec in workflow.spec.bpmn_start_events:
                continue
            boundary = isinstance(task.task_spec, BoundaryEvent)
            repeatable = boundary and not task.task_spec.cancel_activity
            activation = task.internal_data.setdefault(
                "m8f_signal_activation", str(uuid4())
            )
            identity = (
                f"boundary:{task.parent.id}:{task.task_spec.name}"
                if repeatable
                else f"task:{task.id}:{activation}"
            )
            _upsert_subscription(
                session,
                instance.m8f_tenant_id,
                f"pi:{instance.id}:{identity}",
                timestamp,
                signal_name=event.name,
                kind="boundary" if boundary else "catch",
                process_instance_id=instance.id,
                definition_id=instance.bpmn_process_definition_id,
                scope_id=scope_key(scope),
                task_guid=str(task.id),
                task_spec_name=task.task_spec.name,
                process_model_identifier=instance.process_model_identifier,
                repeatable=repeatable,
            )
        for name, start in event_starts(workflow, scope):
            _upsert_subscription(
                session,
                instance.m8f_tenant_id,
                f"pi:{instance.id}:scope:{scope_key(scope)}:event:{name}:{start.name}",
                timestamp,
                signal_name=start.event_definition.name,
                kind="event_subprocess",
                process_instance_id=instance.id,
                definition_id=instance.bpmn_process_definition_id,
                scope_id=scope_key(scope),
                task_guid=None,
                task_spec_name=name,
                process_model_identifier=instance.process_model_identifier,
                repeatable=not interrupts.get(start.name, True),
            )
    session.flush()


def retire_instance_signals(session: Session, instance: ProcessInstanceModel) -> None:
    for row in _subscriptions(
        session, instance.m8f_tenant_id, process_instance_id=instance.id
    ):
        if row.process_instance_id == instance.id:
            row.active = False


def _ensure_registry(session: Session, tenant_id: str, timestamp: datetime) -> None:
    from m8flow_core.models.tenant import M8flowTenantModel

    if session.get(M8flowTenantModel, tenant_id) is None:
        raise NotFoundError(f"Tenant {tenant_id!r} was not found")
    state = session.get(SignalRegistryStateModel, tenant_id)
    if state is not None and state.version == 1:
        return
    if state is not None and state.version != 0:
        raise ValidationError("Unsupported signal registry version")
    from m8flow_core.services.authorization import _get_or_create
    from m8flow_core.services.workflow_engine import _restore_workflow
    from m8flow_core.services.workflow_state import _persist_workflow_recovery_state

    marker = _get_or_create(
        session,
        SignalRegistryStateModel,
        lookup={"m8f_tenant_id": tenant_id},
        values={"m8f_tenant_id": tenant_id, "version": 0},
        conflict_columns=["m8f_tenant_id"],
    )
    if marker.version == 1:
        return
    definitions = list(
        session.scalars(
            select(BpmnProcessDefinitionModel)
            .where(
                BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id,
            )
            .order_by(BpmnProcessDefinitionModel.id)
        )
    )
    for definition in definitions:
        if definition.source_bpmn_xml:
            sync_definition_signals(session, definition, timestamp)
    for instance in session.scalars(
        select(ProcessInstanceModel).where(
            ProcessInstanceModel.m8f_tenant_id == tenant_id,
            ProcessInstanceModel.status.not_in(["complete", "terminated", "error"]),
        )
    ):
        if instance.workflow_state_json:
            workflow = _restore_workflow(instance.workflow_state_json)
            sync_instance_signals(session, instance, workflow, timestamp)
            _persist_workflow_recovery_state(session, instance, workflow)
    marker.version = 1
    session.flush()


def publish_signal(
    session: Session,
    *,
    tenant_id: str,
    signal_name: str,
    user_id: int,
    occurrence_id: str,
    payload: Any = None,
    published_at: datetime | None = None,
) -> SignalPublicationModel:
    from m8flow_core.services.authorization import require_command_authorization

    ensure_user_belongs_to_tenant(session, tenant_id=tenant_id, user_id=user_id)
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key="signal.publish",
        resource_type="tenant",
        resource_id=tenant_id,
    )
    return _publish(
        session,
        tenant_id=tenant_id,
        signal_name=signal_name,
        occurrence_id=occurrence_id,
        payload=payload,
        timestamp=_timestamp(published_at),
        user_id=user_id,
    )


def _publish(
    session: Session,
    *,
    tenant_id: str,
    signal_name: str,
    occurrence_id: str,
    payload: Any,
    timestamp: datetime,
    user_id: int | None = None,
    source_instance_id: int | None = None,
    source_task_guid: str | None = None,
) -> SignalPublicationModel:
    _identifier(signal_name, "Signal name")
    _identifier(occurrence_id, "Occurrence id")
    try:
        normalized = json.loads(json.dumps(payload, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Signal payload must be JSON serializable") from exc
    _ensure_registry(session, tenant_id, timestamp)
    # A no-op UPDATE serializes publication/reservations on SQLite too, where
    # SELECT FOR UPDATE is ignored. The lock lasts until the caller commits.
    session.execute(
        update(SignalRegistryStateModel)
        .where(
            SignalRegistryStateModel.m8f_tenant_id == tenant_id,
        )
        .values(version=SignalRegistryStateModel.version)
    )
    previous = session.scalar(
        select(SignalPublicationModel).where(
            SignalPublicationModel.m8f_tenant_id == tenant_id,
            SignalPublicationModel.occurrence_id == occurrence_id,
        )
    )
    if previous is not None:
        if (
            previous.signal_name != signal_name
            or json.dumps(previous.payload_json, sort_keys=True)
            != json.dumps(normalized, sort_keys=True)
            or previous.source_process_instance_id != source_instance_id
            or previous.published_by_user_id != user_id
        ):
            raise ValidationError(
                "Signal occurrence id was reused with different content"
            )
        return previous
    row = SignalPublicationModel(
        m8f_tenant_id=tenant_id,
        signal_name=signal_name,
        occurrence_id=occurrence_id,
        payload_json=normalized,
        source_process_instance_id=source_instance_id,
        source_task_guid=source_task_guid,
        published_by_user_id=user_id,
        published_at=timestamp,
    )
    session.add(row)
    session.flush()
    for subscription in session.scalars(
        select(SignalSubscriptionModel)
        .where(
            SignalSubscriptionModel.m8f_tenant_id == tenant_id,
            SignalSubscriptionModel.signal_name == signal_name,
            SignalSubscriptionModel.active.is_(True),
            SignalSubscriptionModel.reserved.is_(False),
        )
        .order_by(SignalSubscriptionModel.id)
    ):
        # Enforce exact matching even with case-insensitive database collations.
        if subscription.signal_name != signal_name:
            continue
        if subscription.process_instance_id is not None:
            instance = session.get(
                ProcessInstanceModel, subscription.process_instance_id
            )
            if instance is None or instance.has_terminal_status():
                subscription.active = False
                continue
        if not subscription.repeatable:
            subscription.reserved = True
        session.add(
            SignalDeliveryModel(
                m8f_tenant_id=tenant_id,
                publication_id=row.id,
                subscription_id=subscription.id,
                status="pending",
                attempts=0,
                retry_at=timestamp,
                process_instance_id=subscription.process_instance_id,
            )
        )
    session.flush()
    return row


def publish_runtime_signal(
    session: Session,
    instance: ProcessInstanceModel,
    workflow: Any,
    timestamp: datetime,
    *,
    task: Any,
    payload: Any,
) -> None:
    _ensure_registry(session, instance.m8f_tenant_id, timestamp)
    sync_instance_signals(session, instance, workflow, timestamp)
    sequence = task.internal_data.get("m8f_signal_sequence", 0) + 1
    task.internal_data["m8f_signal_sequence"] = sequence
    publication = _publish(
        session,
        tenant_id=instance.m8f_tenant_id,
        signal_name=task.task_spec.event_definition.name,
        occurrence_id=f"bpmn:{instance.id}:{task.id}:{sequence}",
        payload=payload,
        timestamp=timestamp,
        source_instance_id=instance.id,
        source_task_guid=str(task.id),
    )
    local = list(
        session.scalars(
            select(SignalDeliveryModel).where(
                SignalDeliveryModel.m8f_tenant_id == instance.m8f_tenant_id,
                SignalDeliveryModel.publication_id == publication.id,
                SignalDeliveryModel.process_instance_id == instance.id,
                SignalDeliveryModel.status == "pending",
            )
        )
    )
    _inject_deliveries(session, instance, workflow, publication, local, timestamp)


def _inject_deliveries(
    session: Session,
    instance: ProcessInstanceModel,
    workflow: Any,
    publication: SignalPublicationModel,
    deliveries: list[SignalDeliveryModel],
    timestamp: datetime,
) -> None:
    interrupts = interrupting_starts(instance.bpmn_process_definition.source_bpmn_xml)
    for delivery in deliveries:
        subscription = session.get(SignalSubscriptionModel, delivery.subscription_id)
        delivery.attempts += 1
        received = subscription is not None and deliver_to_subscription(
            workflow,
            subscription,
            publication.payload_json,
            interrupts,
        )
        delivery.status = "delivered" if received else "skipped"
        delivery.delivered_at = timestamp
        delivery.last_error = (
            None if received else "Subscription scope or catcher has ended"
        )
        if subscription is not None and not subscription.repeatable:
            subscription.active = False


def bind_signal_runtime(
    session: Session,
    instance: ProcessInstanceModel,
    workflow: Any,
    timestamp: datetime,
) -> None:
    from defusedxml import ElementTree
    from SpiffWorkflow.bpmn.parser.BpmnParser import full_tag

    definition = instance.bpmn_process_definition
    source = definition.source_bpmn_xml if definition is not None else None
    if source:
        # Older Spiff serializers stored signal ends as NoneEventDefinition.
        # Restore their intended definition from the stored source, preserving
        # the running task tree and every task's execution state.
        ends = {
            node.attrib["id"]
            for node in ElementTree.fromstring(source).iter(full_tag("endEvent"))
            if node.find(full_tag("signalEventDefinition")) is not None
        }
        legacy = [
            task_spec
            for spec in [workflow.spec, *workflow.subprocess_specs.values()]
            for task_spec in spec.task_specs.values()
            if task_spec.name in ends
            and not isinstance(
                getattr(task_spec, "event_definition", None),
                RuntimeSignalDefinition,
            )
        ]
        if legacy:
            from m8flow_core.services.workflow_engine import _build_workflow

            parsed = _build_workflow(
                bpmn_xml=source,
                dmn_xml=definition.source_dmn_xml,
                bpmn_process_id=workflow.spec.name,
            )
            definitions = {
                task_spec.name: task_spec.event_definition
                for spec in [parsed.spec, *parsed.subprocess_specs.values()]
                for task_spec in spec.task_specs.values()
                if task_spec.name in ends
            }
            for task_spec in legacy:
                task_spec.event_definition = definitions[task_spec.name]

    timestamp = _timestamp(timestamp)

    def publish(task: Any, payload: Any) -> None:
        publish_runtime_signal(
            session,
            instance,
            workflow,
            timestamp,
            task=task,
            payload=payload,
        )

    workflow._m8f_signal_publisher = publish


def lock_signal_instance(
    session: Session,
    instance: ProcessInstanceModel,
) -> ProcessInstanceModel:
    """Serialize mutations of signal recipients without changing other runtimes.

    Non-signal service workflows retain their independent failure-persistence
    path, which cannot run while their caller holds a process-row lock.
    """
    definition = instance.bpmn_process_definition
    source = definition.source_bpmn_xml if definition is not None else None
    has_signal = bool(source and "signalEventDefinition" in source)
    if not has_signal:
        has_signal = (
            session.scalar(
                select(SignalSubscriptionModel.id)
                .where(
                    SignalSubscriptionModel.m8f_tenant_id == instance.m8f_tenant_id,
                    SignalSubscriptionModel.process_instance_id == instance.id,
                )
                .limit(1)
            )
            is not None
        )
    if not has_signal:
        return instance
    session.flush()
    session.scalar(
        select(ProcessInstanceModel)
        .where(
            ProcessInstanceModel.m8f_tenant_id == instance.m8f_tenant_id,
            ProcessInstanceModel.id == instance.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance.bpmn_process is not None:
        session.refresh(instance.bpmn_process)
    return instance


def refresh_instance_signal_subscriptions(
    session: Session,
    instance: ProcessInstanceModel,
    timestamp: datetime,
) -> None:
    """Restore eligibility after lifecycle recovery without executing tasks."""
    state = instance.workflow_state_json
    if state is None or "SignalEventDefinition" not in state:
        return
    from m8flow_core.services.workflow_engine import _restore_workflow
    from m8flow_core.services.workflow_state import _persist_workflow_recovery_state

    workflow = _restore_workflow(state)
    sync_instance_signals(session, instance, workflow, timestamp)
    _persist_workflow_recovery_state(session, instance, workflow)
