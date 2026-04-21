from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)


def import_bpmn_process_definition(
    session: Session,
    *,
    tenant_id: str,
    bpmn_identifier: str,
    source_bpmn_xml: str | bytes,
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
    source_bpmn_xml_text = _coerce_xml_text(source_bpmn_xml)
    source_dmn_xml_text = (
        _coerce_xml_text(source_dmn_xml) if source_dmn_xml is not None else None
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

    definition = session.scalar(
        select(BpmnProcessDefinitionModel).where(
            BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id,
            BpmnProcessDefinitionModel.full_process_model_hash
            == resolved_full_process_model_hash,
        )
    )

    if definition is None:
        definition = BpmnProcessDefinitionModel(
            m8f_tenant_id=tenant_id,
            single_process_hash=resolved_single_process_hash,
            full_process_model_hash=resolved_full_process_model_hash,
            bpmn_identifier=bpmn_identifier,
            bpmn_name=bpmn_name,
            properties_json=dict(properties_json or {}),
            source_bpmn_xml=source_bpmn_xml_text,
            source_dmn_xml=source_dmn_xml_text,
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
        definition.bpmn_identifier = bpmn_identifier
        if bpmn_name is not None:
            definition.bpmn_name = bpmn_name
        if properties_json is not None:
            definition.properties_json = dict(properties_json)
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

    session.flush()
    return definition


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _coerce_xml_text(xml: str | bytes) -> str:
    if isinstance(xml, bytes):
        return xml.decode("utf-8")
    return xml
