from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_sample_app.db import sample_app_root
from m8flow_sample_app.seed import lane_owner_usernames_for_tenant
from m8flow_sample_app.shared_m8flow import (
    BackendCatalogSupplementalFile,
    BackendCatalogPublishResult,
    SharedM8flowAuditContext,
    load_process_model_from_m8flow_backend,
    publish_process_model_to_m8flow_backend,
)

DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER = "sample-app/demo-approval"
DEFAULT_DEMO_BPMN_NAME = "Sample App Demo Workflow"
DEFAULT_TIMEOUT_ESCALATION_PROCESS_MODEL_IDENTIFIER = (
    "sample-app/review-timeout-escalation"
)
DEFAULT_TIMEOUT_ESCALATION_BPMN_NAME = "Sample App Review Timeout Escalation"


@dataclass(frozen=True, slots=True)
class DemoDefinitionDeploymentResult:
    definition: BpmnProcessDefinitionModel
    backend_catalog: BackendCatalogPublishResult | None


@dataclass(frozen=True, slots=True)
class BuiltInWorkflowDefinition:
    process_model_identifier: str
    bpmn_name: str
    flow_name: str
    fixture_file_name: str
    version_control_identifier: str
    dmn_fixture_file_name: str | None = None


BUILT_IN_WORKFLOW_DEFINITIONS = {
    "demo": BuiltInWorkflowDefinition(
        process_model_identifier=DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER,
        bpmn_name=DEFAULT_DEMO_BPMN_NAME,
        flow_name="sample_app_demo",
        fixture_file_name="sample_app_demo.bpmn",
        dmn_fixture_file_name="sample_app_demo.dmn",
        version_control_identifier="demo-fixture",
    ),
    "timeout_escalation": BuiltInWorkflowDefinition(
        process_model_identifier=DEFAULT_TIMEOUT_ESCALATION_PROCESS_MODEL_IDENTIFIER,
        bpmn_name=DEFAULT_TIMEOUT_ESCALATION_BPMN_NAME,
        flow_name="sample_app_review_timeout_escalation",
        fixture_file_name="sample_app_review_timeout_escalation.bpmn",
        version_control_identifier="timeout-escalation-fixture",
    ),
}


def list_process_definitions(
    session: Session,
    *,
    tenant_id: str,
) -> list[BpmnProcessDefinitionModel]:
    return list(
        session.scalars(
            select(BpmnProcessDefinitionModel)
            .where(BpmnProcessDefinitionModel.m8f_tenant_id == tenant_id)
            .order_by(BpmnProcessDefinitionModel.id.desc())
        )
    )


def latest_process_definition_ids(
    definitions: Sequence[BpmnProcessDefinitionModel],
) -> set[int]:
    latest_ids: set[int] = set()
    seen_identifiers: set[str] = set()
    for definition in definitions:
        if definition.process_model_identifier in seen_identifiers:
            continue
        seen_identifiers.add(definition.process_model_identifier)
        latest_ids.add(definition.id)
    return latest_ids


def list_latest_process_definitions(
    session: Session,
    *,
    tenant_id: str,
) -> list[BpmnProcessDefinitionModel]:
    definitions = list_process_definitions(session, tenant_id=tenant_id)
    latest_ids = latest_process_definition_ids(definitions)
    return [definition for definition in definitions if definition.id in latest_ids]


def deploy_demo_definition(
    session: Session,
    *,
    tenant_id: str,
    tenant_slug: str | None = None,
    user_id: int,
    audit_context: SharedM8flowAuditContext | None = None,
    process_model_identifier: str = DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER,
    bpmn_name: str = DEFAULT_DEMO_BPMN_NAME,
) -> DemoDefinitionDeploymentResult:
    return deploy_built_in_definition(
        session,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=user_id,
        audit_context=audit_context,
        workflow_key="demo",
        process_model_identifier=process_model_identifier,
        bpmn_name=bpmn_name,
    )


def deploy_definition_from_m8flow_catalog(
    session: Session,
    *,
    tenant_id: str,
    tenant_slug: str | None = None,
    user_id: int,
    audit_context: SharedM8flowAuditContext,
    process_model_identifier: str,
    bpmn_name: str | None = None,
) -> DemoDefinitionDeploymentResult:
    source = load_process_model_from_m8flow_backend(
        audit_context=audit_context,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug or tenant_id,
        process_model_identifier=process_model_identifier,
    )
    return _import_definition(
        session,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=user_id,
        process_model_identifier=process_model_identifier,
        bpmn_name=(bpmn_name.strip() if bpmn_name and bpmn_name.strip() else source.bpmn_name),
        source_bpmn_xml=source.source_bpmn_xml,
        source_dmn_xml=source.source_dmn_xml,
        bpmn_version_control_type="m8flow-backend-catalog",
        bpmn_version_control_identifier=process_model_identifier,
    )


def deploy_definition_from_uploaded_bpmn(
    session: Session,
    *,
    tenant_id: str,
    tenant_slug: str | None = None,
    user_id: int,
    audit_context: SharedM8flowAuditContext | None = None,
    process_model_identifier: str,
    bpmn_name: str | None,
    source_bpmn_xml: str,
    source_file_name: str | None,
) -> DemoDefinitionDeploymentResult:
    now = round(time.time())
    primary_file_name = _normalized_primary_bpmn_file_name(
        source_file_name=source_file_name,
        process_model_identifier=process_model_identifier,
    )
    resolved_bpmn_name = (
        bpmn_name.strip()
        if bpmn_name is not None and bpmn_name.strip()
        else Path(primary_file_name).stem
    )
    definition_result = _import_definition(
        session,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=user_id,
        process_model_identifier=process_model_identifier,
        bpmn_name=resolved_bpmn_name,
        source_bpmn_xml=source_bpmn_xml,
        bpmn_version_control_type="sample-app-upload",
        bpmn_version_control_identifier=primary_file_name,
        created_at_in_seconds=now,
        updated_at_in_seconds=now,
    )
    backend_catalog = publish_process_model_to_m8flow_backend(
        audit_context=audit_context,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug or tenant_id,
        process_model_identifier=process_model_identifier,
        bpmn_name=resolved_bpmn_name,
        source_bpmn_xml=source_bpmn_xml,
        primary_file_name=primary_file_name,
    )
    return DemoDefinitionDeploymentResult(
        definition=definition_result.definition,
        backend_catalog=backend_catalog,
    )


def deploy_timeout_escalation_definition(
    session: Session,
    *,
    tenant_id: str,
    tenant_slug: str | None = None,
    user_id: int,
    audit_context: SharedM8flowAuditContext | None = None,
    process_model_identifier: str = DEFAULT_TIMEOUT_ESCALATION_PROCESS_MODEL_IDENTIFIER,
    bpmn_name: str = DEFAULT_TIMEOUT_ESCALATION_BPMN_NAME,
) -> DemoDefinitionDeploymentResult:
    return deploy_built_in_definition(
        session,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=user_id,
        audit_context=audit_context,
        workflow_key="timeout_escalation",
        process_model_identifier=process_model_identifier,
        bpmn_name=bpmn_name,
    )


def deploy_built_in_definition(
    session: Session,
    *,
    tenant_id: str,
    tenant_slug: str | None = None,
    user_id: int,
    workflow_key: str,
    audit_context: SharedM8flowAuditContext | None = None,
    process_model_identifier: str,
    bpmn_name: str,
) -> DemoDefinitionDeploymentResult:
    now = round(time.time())
    workflow = BUILT_IN_WORKFLOW_DEFINITIONS.get(workflow_key)
    if workflow is None:
        raise ValueError(f"Unknown built-in workflow key: {workflow_key!r}")

    bpmn_path = _built_in_bpmn_path(workflow.fixture_file_name)
    source_bpmn_xml = bpmn_path.read_text(encoding="utf-8")
    source_dmn_xml = (
        _built_in_bpmn_path(workflow.dmn_fixture_file_name).read_text(
            encoding="utf-8"
        )
        if workflow.dmn_fixture_file_name is not None
        else None
    )
    definition_result = _import_definition(
        session,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        user_id=user_id,
        process_model_identifier=process_model_identifier,
        bpmn_name=bpmn_name,
        source_bpmn_xml=source_bpmn_xml,
        source_dmn_xml=source_dmn_xml,
        bpmn_version_control_type="sample-app",
        bpmn_version_control_identifier=workflow.version_control_identifier,
        flow_name=workflow.flow_name,
        created_at_in_seconds=now,
        updated_at_in_seconds=now,
    )
    definition = definition_result.definition
    backend_catalog = publish_process_model_to_m8flow_backend(
        audit_context=audit_context,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug or tenant_id,
        process_model_identifier=process_model_identifier,
        bpmn_name=bpmn_name,
        source_bpmn_xml=source_bpmn_xml,
        primary_file_name=bpmn_path.name,
        supplemental_files=(
            (
                BackendCatalogSupplementalFile(
                    file_name=workflow.dmn_fixture_file_name,
                    contents=source_dmn_xml,
                ),
            )
            if workflow.dmn_fixture_file_name is not None and source_dmn_xml is not None
            else ()
        ),
    )
    return DemoDefinitionDeploymentResult(
        definition=definition,
        backend_catalog=backend_catalog,
    )


def _import_definition(
    session: Session,
    *,
    tenant_id: str,
    tenant_slug: str | None,
    user_id: int,
    process_model_identifier: str,
    bpmn_name: str,
    source_bpmn_xml: str,
    source_dmn_xml: str | None = None,
    bpmn_version_control_type: str | None,
    bpmn_version_control_identifier: str | None,
    flow_name: str | None = None,
    created_at_in_seconds: int | None = None,
    updated_at_in_seconds: int | None = None,
) -> DemoDefinitionDeploymentResult:
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_identifier=process_model_identifier,
            user_id=user_id,
            bpmn_name=bpmn_name,
            source_bpmn_xml=source_bpmn_xml,
            source_dmn_xml=source_dmn_xml,
            properties_json={
                "version": 1,
                "flow": flow_name or process_model_identifier,
                "lane_owners": lane_owner_usernames_for_tenant(
                    tenant_id,
                    tenant_slug=tenant_slug,
                ),
            },
            bpmn_version_control_type=bpmn_version_control_type,
            bpmn_version_control_identifier=bpmn_version_control_identifier,
            created_at_in_seconds=created_at_in_seconds,
            updated_at_in_seconds=updated_at_in_seconds,
        ),
    )
    return DemoDefinitionDeploymentResult(
        definition=definition,
        backend_catalog=None,
    )


def _built_in_bpmn_path(file_name: str) -> Path:
    return sample_app_root() / "fixtures" / file_name


def _normalized_primary_bpmn_file_name(
    *,
    source_file_name: str | None,
    process_model_identifier: str,
) -> str:
    fallback_name = f"{process_model_identifier.rsplit('/', 1)[-1] or 'workflow'}.bpmn"
    if source_file_name is None or not source_file_name.strip():
        return fallback_name

    candidate = Path(source_file_name.strip()).name
    if not candidate or candidate in {".", ".."}:
        return fallback_name
    if Path(candidate).suffix.lower() != ".bpmn":
        stem = Path(candidate).stem or Path(fallback_name).stem
        return f"{stem}.bpmn"
    return candidate
