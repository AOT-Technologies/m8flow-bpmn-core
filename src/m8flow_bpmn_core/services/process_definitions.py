from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Any

from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.errors import ValidationError
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_bpmn_core.models.process_model_bpmn_version import (
    ProcessModelBpmnVersionModel,
)
from m8flow_bpmn_core.services.authorization import (
    PROCESS_DEFINITION_IMPORT_COMMAND,
    require_command_authorization,
)
from m8flow_bpmn_core.services.tenant_users import ensure_user_belongs_to_tenant
from m8flow_bpmn_core.services.workflow_runtime import (
    _sync_lane_owner_group_assignments,
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
    single_process_hash: str | None = None,
    full_process_model_hash: str | None = None,
    created_at_in_seconds: int | None = None,
    updated_at_in_seconds: int | None = None,
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
        target_uri=f"/process-definitions/{bpmn_identifier}",
    )
    source_bpmn_xml_text = _coerce_xml_text(source_bpmn_xml)
    source_dmn_xml_text = (
        _coerce_xml_text(source_dmn_xml) if source_dmn_xml is not None else None
    )
    _validate_bpmn_source(
        bpmn_xml_text=source_bpmn_xml_text,
        dmn_xml_text=source_dmn_xml_text,
    )
    resolved_full_process_model_hash = (
        full_process_model_hash
        if full_process_model_hash is not None
        else _hash_text(source_bpmn_xml_text)
    )
    resolved_single_process_hash = (
        single_process_hash
        if single_process_hash is not None
        else _hash_text(f"single::{source_bpmn_xml_text}")
    )
    resolved_process_definition_identifier = _extract_process_identifier(
        source_bpmn_xml_text
    )

    definition = session.scalar(
        select(BpmnProcessDefinitionModel).where(
            BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id,
            BpmnProcessDefinitionModel.full_process_model_hash
            == resolved_full_process_model_hash,
        )
    )
    resolved_properties_json = dict(properties_json or {})

    if definition is None:
        definition = BpmnProcessDefinitionModel(
            m8f_tenant_id=tenant_id,
            single_process_hash=resolved_single_process_hash,
            full_process_model_hash=resolved_full_process_model_hash,
            bpmn_identifier=resolved_process_definition_identifier,
            bpmn_name=bpmn_name,
            properties_json=resolved_properties_json,
            bpmn_version_control_type=bpmn_version_control_type,
            bpmn_version_control_identifier=bpmn_version_control_identifier,
            created_at_in_seconds=created_at_in_seconds,
            updated_at_in_seconds=(
                updated_at_in_seconds
                if updated_at_in_seconds is not None
                else created_at_in_seconds
            ),
        )
        session.add(definition)
    else:
        definition.single_process_hash = resolved_single_process_hash
        definition.full_process_model_hash = resolved_full_process_model_hash
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
            definition.bpmn_version_control_identifier = (
                bpmn_version_control_identifier
            )
        if created_at_in_seconds is not None:
            definition.created_at_in_seconds = created_at_in_seconds
        if updated_at_in_seconds is not None:
            definition.updated_at_in_seconds = updated_at_in_seconds
        elif created_at_in_seconds is not None:
            definition.updated_at_in_seconds = created_at_in_seconds
    definition.process_model_identifier = bpmn_identifier
    definition.source_bpmn_xml = source_bpmn_xml_text
    if source_dmn_xml is not None:
        definition.source_dmn_xml = source_dmn_xml_text

    session.flush()
    snapshot_timestamp = (
        updated_at_in_seconds
        if updated_at_in_seconds is not None
        else created_at_in_seconds
        if created_at_in_seconds is not None
        else round(time.time())
    )
    _ensure_bpmn_version_snapshot(
        session,
        tenant_id=tenant_id,
        process_model_identifier=bpmn_identifier,
        bpmn_xml_text=source_bpmn_xml_text,
        occurred_at=snapshot_timestamp,
    )
    _sync_timer_start_scheduler_jobs_for_definition(
        session,
        process_definition=definition,
        occurred_at=snapshot_timestamp,
    )
    _sync_lane_owner_group_assignments(
        session,
        tenant_id=tenant_id,
        lane_owners=resolved_properties_json.get("lane_owners"),
    )
    return definition


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _coerce_xml_text(xml: str | bytes) -> str:
    if isinstance(xml, bytes):
        return xml.decode("utf-8")
    return xml


def _ensure_bpmn_version_snapshot(
    session: Session,
    *,
    tenant_id: str,
    process_model_identifier: str,
    bpmn_xml_text: str,
    occurred_at: int,
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
                created_at_in_seconds=occurred_at,
            )
        )
        session.flush()


def _extract_process_identifier(bpmn_xml_text: str) -> str:
    parser = SpiffBpmnParser(validator=None)
    parser.add_bpmn_str(
        bpmn_xml_text.encode("utf-8"),
        filename="definition-process-selector.bpmn",
    )
    process_ids = parser.get_process_ids()
    if not process_ids:
        raise ValidationError(
            "BPMN source does not contain any executable processes"
        )
    if len(process_ids) != 1:
        raise ValidationError(
            "A BPMN file must contain exactly one executable process when it is "
            "imported as a single definition"
        )
    return process_ids[0]


def _validate_bpmn_source(
    *,
    bpmn_xml_text: str,
    dmn_xml_text: str | None,
) -> None:
    """Parse the BPMN (and optional DMN) source to verify it is well-formed.

    Catches malformed XML, missing executable processes, and DMN parse errors at
    import time, instead of letting them surface during workflow execution.
    """
    parser = SpiffBpmnParser(validator=None)
    try:
        parser.add_bpmn_str(
            bpmn_xml_text.encode("utf-8"),
            filename="import-validation.bpmn",
        )
        if dmn_xml_text is not None:
            parser.add_dmn_str(
                dmn_xml_text.encode("utf-8"),
                filename="import-validation.dmn",
            )
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            f"BPMN/DMN source is not parseable: {exc}"
        ) from exc

    try:
        process_ids = parser.get_process_ids()
    except Exception as exc:
        raise ValidationError(
            f"BPMN source does not declare any usable process: {exc}"
        ) from exc

    if not process_ids:
        raise ValidationError(
            "BPMN source does not contain any executable processes"
        )
