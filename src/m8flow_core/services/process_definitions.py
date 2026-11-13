from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from defusedxml import ElementTree
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from m8flow_core.errors import ValidationError
from m8flow_core.models.bpmn_process_definition import (
    COLLABORATION_PROPERTY_KEY,
    BpmnProcessDefinitionModel,
)
from m8flow_core.models.process_model_bpmn_version import (
    ProcessModelBpmnVersionModel,
)
from m8flow_core.models.tenant import M8flowTenantModel
from m8flow_core.services.authorization import (
    PROCESS_DEFINITION_IMPORT_COMMAND,
    require_command_authorization,
)
from m8flow_core.services.collaborations import (
    collaboration_parser_source,
    lock_collaboration_definition,
    read_collaboration,
)
from m8flow_core.services.lane_assignment import (
    enroll_definition_lane_owners,
    validate_initiator_lanes,
)
from m8flow_core.services.tenant_users import ensure_user_belongs_to_tenant
from m8flow_core.services.workflow_runtime import (
    _collect_timer_start_payloads_for_definition,
    _RuntimeSpiffBpmnParser,
    _sync_timer_start_scheduler_jobs_for_definition,
)


def import_bpmn_process_definition(
    session: Session,
    *,
    tenant_id: str,
    bpmn_identifier: str,
    source_bpmn_xml: str | bytes,
    user_id: int,
    source_dmn_xml: str | bytes | None = None,
    bpmn_name: str | None = None,
    properties_json: Mapping[str, Any] | None = None,
    bpmn_version_control_type: str | None = None,
    bpmn_version_control_identifier: str | None = None,
    process_xml_digest: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> BpmnProcessDefinitionModel:
    ensure_user_belongs_to_tenant(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    require_command_authorization(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        command_key=PROCESS_DEFINITION_IMPORT_COMMAND,
        resource_type="process_definition",
        resource_id=bpmn_identifier,
    )
    source_bpmn_xml_text = _coerce_xml_text(source_bpmn_xml)
    source_dmn_xml_text = (
        _coerce_xml_text(source_dmn_xml) if source_dmn_xml is not None else None
    )
    source_bytes = source_bpmn_xml_text.encode("utf-8")
    collaboration = read_collaboration(source_bytes)
    parser_source = (
        collaboration_parser_source(source_bytes) if collaboration else source_bytes
    )
    participant_ids = collaboration["executable_process_ids"] if collaboration else None
    process_ids = _validate_bpmn_source(
        bpmn_xml=parser_source,
        dmn_xml_text=source_dmn_xml_text,
        participant_ids=participant_ids,
    )
    if collaboration is None and len(process_ids) != 1:
        raise ValidationError(
            "A BPMN file must contain one executable process or a collaboration"
        )
    actual_digest = _hash_text(source_bpmn_xml_text)
    if collaboration is not None and process_xml_digest not in (None, actual_digest):
        raise ValidationError("Collaboration digest must match the BPMN source")

    from m8flow_core.services.signal_registry import validate_signal_source

    for process_id in participant_ids or process_ids:
        validate_signal_source(
            source_bpmn_xml_text,
            source_dmn_xml_text,
            process_id,
            collaboration=collaboration,
        )
    resolved_process_xml_digest = (
        process_xml_digest if process_xml_digest is not None else actual_digest
    )
    resolved_process_definition_identifier = (
        collaboration["id"] if collaboration else process_ids[0]
    )

    if collaboration is not None:
        # Serialize the first import, before a deployment row exists.
        session.execute(
            update(M8flowTenantModel)
            .where(M8flowTenantModel.id == tenant_id)
            .values(id=M8flowTenantModel.id)
            .execution_options(synchronize_session=False)
        )

    definition = session.scalar(
        select(BpmnProcessDefinitionModel)
        .where(
            BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id,
            BpmnProcessDefinitionModel.process_xml_digest
            == resolved_process_xml_digest,
        )
        .execution_options(populate_existing=True)
    )
    if definition is not None and (collaboration or definition.collaboration_metadata):
        lock_collaboration_definition(session, definition)
        if (
            collaboration is None
            or definition.collaboration_metadata is None
            or definition.source_bpmn_xml != source_bpmn_xml_text
            or definition.process_model_identifier != bpmn_identifier
            or (
                source_dmn_xml is not None
                and definition.source_dmn_xml != source_dmn_xml_text
            )
        ):
            raise ValidationError(
                "Import conflicts with an existing collaboration version"
            )
    resolved_properties_json = dict(properties_json or {})
    resolved_properties_json.pop(COLLABORATION_PROPERTY_KEY, None)
    effective_properties_json = (
        resolved_properties_json
        if properties_json is not None or definition is None
        else dict(definition.properties_json or {})
    )
    validate_initiator_lanes(
        effective_properties_json,
        bpmn_lane_names=_bpmn_lane_names(source_bpmn_xml_text),
    )

    is_new_definition = definition is None
    if definition is None:
        definition = BpmnProcessDefinitionModel(
            m8f_tenant_id=tenant_id,
            process_xml_digest=resolved_process_xml_digest,
            bpmn_identifier=resolved_process_definition_identifier,
            bpmn_name=bpmn_name,
            properties_json=resolved_properties_json,
            bpmn_version_control_type=bpmn_version_control_type,
            bpmn_version_control_identifier=bpmn_version_control_identifier,
            created_at=created_at,
            updated_at=(updated_at if updated_at is not None else created_at),
        )
    else:
        definition.process_xml_digest = resolved_process_xml_digest
        definition.bpmn_identifier = resolved_process_definition_identifier
        if bpmn_name is not None:
            definition.bpmn_name = bpmn_name
        if properties_json is not None:
            definition.properties_json = resolved_properties_json
        definition.source_bpmn_xml = source_bpmn_xml_text
        if source_dmn_xml is not None:
            definition.source_dmn_xml = source_dmn_xml_text
        if bpmn_version_control_type is not None:
            definition.bpmn_version_control_type = bpmn_version_control_type
        if bpmn_version_control_identifier is not None:
            definition.bpmn_version_control_identifier = bpmn_version_control_identifier
        if created_at is not None:
            definition.created_at = created_at
        if updated_at is not None:
            definition.updated_at = updated_at
        elif created_at is not None:
            definition.updated_at = created_at
    definition.process_model_identifier = bpmn_identifier
    definition.source_bpmn_xml = source_bpmn_xml_text
    if source_dmn_xml is not None:
        definition.source_dmn_xml = source_dmn_xml_text
    if collaboration is not None:
        definition.properties_json = {
            **definition.properties_json,
            COLLABORATION_PROPERTY_KEY: collaboration,
        }

    timer_start_payloads = None
    if is_new_definition:
        if collaboration is not None:
            try:
                timer_start_payloads = _collect_timer_start_payloads_for_definition(
                    definition
                )
            except Exception as exc:
                raise ValidationError(
                    f"Invalid collaboration timer start: {exc}"
                ) from exc
        session.add(definition)
    session.flush()
    snapshot_timestamp = (
        updated_at
        if updated_at is not None
        else created_at
        if created_at is not None
        else datetime.now(UTC)
    )
    _ensure_bpmn_version_snapshot(
        session,
        tenant_id=tenant_id,
        process_model_identifier=bpmn_identifier,
        bpmn_xml_text=source_bpmn_xml_text,
        occurred_at=snapshot_timestamp,
    )
    if collaboration is None or is_new_definition:
        _sync_timer_start_scheduler_jobs_for_definition(
            session,
            process_definition=definition,
            occurred_at=snapshot_timestamp,
            timer_start_payloads=timer_start_payloads,
        )

    from m8flow_core.services.signal_registry import (
        _ensure_registry,
        sync_definition_signals,
    )

    _ensure_registry(session, tenant_id, snapshot_timestamp)
    sync_definition_signals(session, definition, snapshot_timestamp)
    enroll_definition_lane_owners(
        session,
        tenant_id=tenant_id,
        properties_json=effective_properties_json,
    )
    return definition


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _coerce_xml_text(xml: str | bytes) -> str:
    if isinstance(xml, bytes):
        try:
            return xml.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("BPMN and DMN source must use UTF-8") from exc
    return xml


def _bpmn_lane_names(bpmn_xml_text: str) -> list[str]:
    """Return the ``name`` of every lane element in the BPMN source."""
    root = ElementTree.fromstring(bpmn_xml_text.encode("utf-8"))
    return [
        element.get("name") or ""
        for element in root.iter()
        if isinstance(element.tag, str) and element.tag.rsplit("}", 1)[-1] == "lane"
    ]


def _ensure_bpmn_version_snapshot(
    session: Session,
    *,
    tenant_id: str,
    process_model_identifier: str,
    bpmn_xml_text: str,
    occurred_at: datetime,
) -> None:
    bpmn_xml_hash = _hash_text(bpmn_xml_text)
    snapshot = session.scalar(
        select(ProcessModelBpmnVersionModel).where(
            ProcessModelBpmnVersionModel.m8f_tenant_id == tenant_id,
            ProcessModelBpmnVersionModel.process_model_identifier
            == process_model_identifier,
            ProcessModelBpmnVersionModel.bpmn_xml_hash == bpmn_xml_hash,
        )
    )
    if snapshot is None:
        session.add(
            ProcessModelBpmnVersionModel(
                m8f_tenant_id=tenant_id,
                process_model_identifier=process_model_identifier,
                bpmn_xml_hash=bpmn_xml_hash,
                bpmn_xml_file_contents=bpmn_xml_text,
                created_at=occurred_at,
            )
        )
        session.flush()


def _validate_bpmn_source(
    *,
    bpmn_xml: bytes,
    dmn_xml_text: str | None,
    participant_ids: list[str] | None,
) -> list[str]:
    """Parse the BPMN (and optional DMN) source to verify it is well-formed.

    Catches malformed XML, missing executable processes, and DMN parse errors at
    import time, instead of letting them surface during workflow execution.
    """
    parser = _RuntimeSpiffBpmnParser(
        validator=None, use_noop_dmn_engine=dmn_xml_text is None
    )
    try:
        parser.add_bpmn_str(
            bpmn_xml,
            filename="import-validation.bpmn",
        )
        if dmn_xml_text is not None:
            parser.add_dmn_str(
                dmn_xml_text.encode("utf-8"),
                filename="import-validation.dmn",
            )
        for process_id in participant_ids or ():
            parser.get_spec(process_id)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f"BPMN/DMN source is not parseable: {exc}") from exc

    try:
        process_ids = parser.get_process_ids()
    except Exception as exc:
        raise ValidationError(
            f"BPMN source does not declare any usable process: {exc}"
        ) from exc

    if not process_ids:
        raise ValidationError("BPMN source does not contain any executable processes")
    if parser.has_compensation:
        try:
            for process_id in [*process_ids, *parser.compensation_handlers.values()]:
                parser.get_spec(process_id)
                parser.get_subprocess_specs(process_id)
        except Exception as exc:
            raise ValidationError(
                f"Compensation BPMN specs are not parseable: {exc}"
            ) from exc
    return list(process_ids)
