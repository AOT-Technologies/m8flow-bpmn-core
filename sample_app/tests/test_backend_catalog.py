from __future__ import annotations

import json
from pathlib import Path

from m8flow_sample_app.db import sample_app_root
from m8flow_sample_app.shared_m8flow import (
    SharedM8flowAuditContext,
    publish_process_model_to_m8flow_backend,
)


def test_publish_process_model_to_m8flow_backend_creates_catalog_files(
    tmp_path: Path,
) -> None:
    result = publish_process_model_to_m8flow_backend(
        audit_context=_shared_audit_context(tmp_path),
        tenant_id="org-alpha",
        tenant_slug="sample-tenant-alpha",
        process_model_identifier="sample-app/demo-approval",
        bpmn_name="Sample App Demo Workflow",
        source_bpmn_xml=_demo_bpmn_xml(),
        primary_file_name="sample_app_demo.bpmn",
    )

    assert result is not None
    assert result.status == "created"
    assert result.tenant_root == "org-alpha"
    assert result.process_group_id == "sample-app"
    assert result.process_model_id == "demo-approval"

    group_json = tmp_path / "org-alpha" / "sample-app" / "process_group.json"
    model_json = (
        tmp_path
        / "org-alpha"
        / "sample-app"
        / "demo-approval"
        / "process_model.json"
    )
    bpmn_path = (
        tmp_path
        / "org-alpha"
        / "sample-app"
        / "demo-approval"
        / "sample_app_demo.bpmn"
    )
    assert group_json.exists()
    assert model_json.exists()
    assert bpmn_path.exists()

    model_payload = json.loads(model_json.read_text(encoding="utf-8"))
    assert model_payload["display_name"] == "Sample App Demo Workflow"
    assert model_payload["primary_file_name"] == "sample_app_demo.bpmn"
    assert model_payload["primary_process_id"] == "Process_sample_app_demo"


def test_publish_process_model_to_m8flow_backend_returns_unchanged_when_current(
    tmp_path: Path,
) -> None:
    first = publish_process_model_to_m8flow_backend(
        audit_context=_shared_audit_context(tmp_path),
        tenant_id="org-alpha",
        tenant_slug="sample-tenant-alpha",
        process_model_identifier="sample-app/demo-approval",
        bpmn_name="Sample App Demo Workflow",
        source_bpmn_xml=_demo_bpmn_xml(),
        primary_file_name="sample_app_demo.bpmn",
    )
    second = publish_process_model_to_m8flow_backend(
        audit_context=_shared_audit_context(tmp_path),
        tenant_id="org-alpha",
        tenant_slug="sample-tenant-alpha",
        process_model_identifier="sample-app/demo-approval",
        bpmn_name="Sample App Demo Workflow",
        source_bpmn_xml=_demo_bpmn_xml(),
        primary_file_name="sample_app_demo.bpmn",
    )

    assert first is not None and first.status == "created"
    assert second is not None and second.status == "unchanged"


def test_publish_process_model_to_m8flow_backend_updates_existing_model(
    tmp_path: Path,
) -> None:
    publish_process_model_to_m8flow_backend(
        audit_context=_shared_audit_context(tmp_path),
        tenant_id="org-alpha",
        tenant_slug="sample-tenant-alpha",
        process_model_identifier="sample-app/demo-approval",
        bpmn_name="Sample App Demo Workflow",
        source_bpmn_xml=_demo_bpmn_xml(),
        primary_file_name="sample_app_demo.bpmn",
    )
    updated = publish_process_model_to_m8flow_backend(
        audit_context=_shared_audit_context(tmp_path),
        tenant_id="org-alpha",
        tenant_slug="sample-tenant-alpha",
        process_model_identifier="sample-app/demo-approval",
        bpmn_name="Updated Sample App Demo Workflow",
        source_bpmn_xml=_demo_bpmn_xml(),
        primary_file_name="sample_app_demo.bpmn",
    )

    assert updated is not None
    assert updated.status == "updated"

    model_json = (
        tmp_path
        / "org-alpha"
        / "sample-app"
        / "demo-approval"
        / "process_model.json"
    )
    model_payload = json.loads(model_json.read_text(encoding="utf-8"))
    assert model_payload["display_name"] == "Updated Sample App Demo Workflow"


def test_publish_process_model_to_m8flow_backend_skips_invalid_identifier(
    tmp_path: Path,
) -> None:
    result = publish_process_model_to_m8flow_backend(
        audit_context=_shared_audit_context(tmp_path),
        tenant_id="org-alpha",
        tenant_slug="sample-tenant-alpha",
        process_model_identifier="demo-approval",
        bpmn_name="Sample App Demo Workflow",
        source_bpmn_xml=_demo_bpmn_xml(),
        primary_file_name="sample_app_demo.bpmn",
    )

    assert result is not None
    assert result.status == "skipped"
    assert any("<group>/<model>" in warning for warning in result.warnings)
    assert not (tmp_path / "org-alpha").exists()


def _shared_audit_context(process_models_root: Path) -> SharedM8flowAuditContext:
    return SharedM8flowAuditContext(
        mode="shared",
        requested_mode="shared",
        database_name="postgres",
        process_models_root=process_models_root,
        backend_container_name="m8flow-m8flow-backend-1",
        backend_tenant_root_override=None,
        warnings=(),
    )


def _demo_bpmn_xml() -> str:
    fixture_path = sample_app_root() / "fixtures" / "sample_app_demo.bpmn"
    return fixture_path.read_text(encoding="utf-8")
