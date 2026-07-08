from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from m8flow_bpmn_core import api
from m8flow_bpmn_core.models.bpmn_process_definition import (
    BpmnProcessDefinitionModel,
)
from m8flow_sample_app.db import sample_app_root
from m8flow_sample_app.seed import lane_owner_usernames_for_tenant
from m8flow_sample_app.shared_m8flow import (
    BackendCatalogPublishResult,
    SharedM8flowAuditContext,
    publish_process_model_to_m8flow_backend,
)

DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER = "sample-app/demo-approval"
DEFAULT_DEMO_BPMN_NAME = "Sample App Demo Workflow"


@dataclass(frozen=True, slots=True)
class DemoDefinitionDeploymentResult:
    definition: BpmnProcessDefinitionModel
    backend_catalog: BackendCatalogPublishResult | None


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
    now = round(time.time())
    source_bpmn_xml = _demo_bpmn_path().read_text(encoding="utf-8")
    definition = api.execute_command(
        session,
        api.ImportBpmnProcessDefinitionCommand(
            tenant_id=tenant_id,
            bpmn_identifier=process_model_identifier,
            user_id=user_id,
            bpmn_name=bpmn_name,
            source_bpmn_xml=source_bpmn_xml,
            properties_json={
                "version": 1,
                "flow": "sample_app_demo",
                "lane_owners": lane_owner_usernames_for_tenant(
                    tenant_id,
                    tenant_slug=tenant_slug,
                ),
            },
            bpmn_version_control_type="sample-app",
            bpmn_version_control_identifier="demo-fixture",
            created_at_in_seconds=now,
            updated_at_in_seconds=now,
        ),
    )
    backend_catalog = publish_process_model_to_m8flow_backend(
        audit_context=audit_context,
        tenant_id=tenant_id,
        tenant_slug=tenant_slug or tenant_id,
        process_model_identifier=process_model_identifier,
        bpmn_name=bpmn_name,
        source_bpmn_xml=source_bpmn_xml,
        primary_file_name=_demo_bpmn_path().name,
    )
    return DemoDefinitionDeploymentResult(
        definition=definition,
        backend_catalog=backend_catalog,
    )


def _demo_bpmn_path() -> Path:
    return sample_app_root() / "fixtures" / "sample_app_demo.bpmn"
