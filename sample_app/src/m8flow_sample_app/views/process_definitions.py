from __future__ import annotations

from html import escape

from flask import Flask, current_app, flash, redirect, request, url_for

from m8flow_bpmn_core import api
from m8flow_sample_app.auth import get_active_identity
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.shared_m8flow import (
    SHARED_M8FLOW_AUDIT_CONTEXT_KEY,
    BackendCatalogPublishResult,
    SharedM8flowAuditContext,
)
from m8flow_sample_app.ui import render_page
from m8flow_sample_app.workflows.deploy import (
    DEFAULT_DEMO_BPMN_NAME,
    DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER,
    deploy_demo_definition,
    list_process_definitions,
)


def register_process_definition_routes(app: Flask) -> None:
    @app.get("/process-definitions")
    def process_definitions_page():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            definitions = list_process_definitions(
                db_session,
                tenant_id=identity.tenant.id,
            )
            rows = "".join(
                f"""
<tr>
  <td>{definition.id}</td>
  <td>{escape(definition.process_model_identifier)}</td>
  <td>{escape(definition.bpmn_identifier)}</td>
  <td>{escape(definition.bpmn_name or '')}</td>
  <td>{definition.updated_at_in_seconds or ''}</td>
  <td><a href="{escape(url_for("start_workflow", definition_id=definition.id))}">Start from this definition</a></td>
</tr>
"""
                for definition in definitions
            )
            definitions_html = (
                f"""
<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>Process model identifier</th>
      <th>BPMN process id</th>
      <th>Name</th>
      <th>Updated</th>
      <th>Action</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
"""
                if definitions
                else "<p>No process definitions are stored for this tenant yet.</p>"
            )

            body = f"""
<p>This screen stores workflow definitions in the library tables through
<code>ImportBpmnProcessDefinitionCommand</code>.</p>
<p>In shared m8flow audit mode, identifiers in
<code>&lt;group&gt;/&lt;model&gt;</code> format are also published into the
local m8flow backend process-model catalog so they appear in the m8flow UI.</p>
<form method="post" action="{escape(url_for("deploy_demo_definition_action"))}">
  <label for="process_model_identifier">Process model identifier</label><br />
  <input
    id="process_model_identifier"
    name="process_model_identifier"
    type="text"
    value="{escape(DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER)}"
  /><br /><br />
  <label for="bpmn_name">Display name</label><br />
  <input
    id="bpmn_name"
    name="bpmn_name"
    type="text"
    value="{escape(DEFAULT_DEMO_BPMN_NAME)}"
  /><br /><br />
  <button type="submit">Deploy built-in demo workflow</button>
</form>
<p>The built-in demo uses a reimbursement BPMN from
<code>sample_app/fixtures/sample_app_demo.bpmn</code>. It routes amounts over
1000 through Finance before the final review, skips the final review when
Finance rejects the request, and sends an outcome HTML email through the
connector-proxy SMTP service task using tenant-scoped Mailtrap
secrets.</p>
<h2>Stored definitions</h2>
{definitions_html}
"""
            return render_page(
                "Process Definitions",
                body,
                identity=identity,
            )

    @app.post("/process-definitions/deploy-demo")
    def deploy_demo_definition_action():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            process_model_identifier = (
                request.form.get("process_model_identifier", "").strip()
                or DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER
            )
            bpmn_name = (
                request.form.get("bpmn_name", "").strip() or DEFAULT_DEMO_BPMN_NAME
            )
            audit_context = current_app.extensions.get(
                SHARED_M8FLOW_AUDIT_CONTEXT_KEY
            )

            try:
                deployment = deploy_demo_definition(
                    db_session,
                    tenant_id=identity.tenant.id,
                    tenant_slug=identity.tenant.slug,
                    user_id=identity.user.id,
                    audit_context=(
                        audit_context
                        if isinstance(audit_context, SharedM8flowAuditContext)
                        else None
                    ),
                    process_model_identifier=process_model_identifier,
                    bpmn_name=bpmn_name,
                )
            except api.BpmnCoreError as exc:
                flash(str(exc), "error")
            else:
                flash(
                    f"Definition {deployment.definition.id} deployed for process model "
                    f"{process_model_identifier}.",
                    "success",
                )
                _flash_backend_catalog_result(deployment.backend_catalog)
            return redirect(url_for("process_definitions_page"))


def _flash_backend_catalog_result(
    result: BackendCatalogPublishResult | None,
) -> None:
    if result is None:
        return

    model_identifier = (
        f"{result.process_group_id}/{result.process_model_id}"
        if result.process_group_id and result.process_model_id
        else None
    )
    if result.status == "created" and model_identifier is not None:
        flash(
            "Published the workflow into the local m8flow backend catalog as "
            f"'{model_identifier}' for tenant root '{result.tenant_root}'.",
            "success",
        )
    elif result.status == "updated" and model_identifier is not None:
        flash(
            "Refreshed the local m8flow backend catalog entry "
            f"'{model_identifier}' for tenant root '{result.tenant_root}'.",
            "success",
        )
    elif result.status == "unchanged" and model_identifier is not None:
        flash(
            "The local m8flow backend catalog already matched "
            f"'{model_identifier}' for tenant root '{result.tenant_root}'.",
            "success",
        )

    for warning in result.warnings:
        flash(warning, "warning")
