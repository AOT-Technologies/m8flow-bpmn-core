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
from m8flow_sample_app.ui import format_timestamp, render_page
from m8flow_sample_app.workflows.deploy import (
    DEFAULT_DEMO_BPMN_NAME,
    DEFAULT_DEMO_PROCESS_MODEL_IDENTIFIER,
    DEFAULT_TIMEOUT_ESCALATION_BPMN_NAME,
    DEFAULT_TIMEOUT_ESCALATION_PROCESS_MODEL_IDENTIFIER,
    deploy_definition_from_m8flow_catalog,
    deploy_definition_from_uploaded_bpmn,
    deploy_demo_definition,
    deploy_timeout_escalation_definition,
    latest_process_definition_ids,
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
            startable_definition_ids = latest_process_definition_ids(definitions)
            rows = "".join(
                f"""
<tr>
  <td>{definition.id}</td>
  <td>{escape(definition.process_model_identifier)}</td>
  <td>{escape(definition.bpmn_identifier)}</td>
  <td>{escape(definition.bpmn_name or '')}</td>
  <td>{format_timestamp(definition.updated_at_in_seconds)}</td>
  <td>{
      (
          f'<a href="{escape(url_for("start_workflow", definition_id=definition.id))}">Start from this definition</a>'
          if definition.id in startable_definition_ids
          else "History only"
      )
  }</td>
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
<h2>Built-in reimbursement workflow</h2>
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
<code>sample_app/fixtures/sample_app_demo.bpmn</code> plus a DMN decision from
<code>sample_app/fixtures/sample_app_demo.dmn</code>. The DMN decides whether a
request requires Finance review before the final review, the flow skips the
final review when Finance rejects the request, and it sends an outcome HTML
email through the connector-proxy SMTP service task using m8flow-style
<code>M8FLOW_SECRET:&lt;NAME&gt;</code> parameter references backed by
tenant-scoped SMTP secrets.</p>
<h2>Built-in timeout escalation workflow</h2>
<form method="post" action="{escape(url_for("deploy_timeout_escalation_definition_action"))}">
  <label for="timeout_process_model_identifier">Process model identifier</label><br />
  <input
    id="timeout_process_model_identifier"
    name="process_model_identifier"
    type="text"
    value="{escape(DEFAULT_TIMEOUT_ESCALATION_PROCESS_MODEL_IDENTIFIER)}"
  /><br /><br />
  <label for="timeout_bpmn_name">Display name</label><br />
  <input
    id="timeout_bpmn_name"
    name="bpmn_name"
    type="text"
    value="{escape(DEFAULT_TIMEOUT_ESCALATION_BPMN_NAME)}"
  /><br /><br />
  <button type="submit">Deploy built-in timeout escalation workflow</button>
</form>
<p>The timeout escalation workflow uses
<code>sample_app/fixtures/sample_app_review_timeout_escalation.bpmn</code>.
It starts with an Operations manual review task and uses an interrupting
boundary timer to escalate the work to the Supervisor lane if the initial
review is still open two minutes after process start.</p>
<p>The table below keeps every stored definition for history, but only the
latest definition for each process model can be started from the sample app.</p>
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

    @app.post("/process-definitions/import-upload")
    def import_uploaded_definition_action():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            process_model_identifier = (
                request.form.get("process_model_identifier", "").strip()
            )
            bpmn_name = request.form.get("bpmn_name", "").strip() or None
            uploaded_file = request.files.get("bpmn_file")
            if uploaded_file is None or not (uploaded_file.filename or "").strip():
                flash("Select a BPMN file to import.", "error")
                return redirect(url_for("process_definitions_page"))

            try:
                source_bpmn_xml = uploaded_file.read().decode("utf-8")
            except UnicodeDecodeError:
                flash("The uploaded BPMN file must be UTF-8 encoded text.", "error")
                return redirect(url_for("process_definitions_page"))

            audit_context = current_app.extensions.get(
                SHARED_M8FLOW_AUDIT_CONTEXT_KEY
            )
            try:
                deployment = deploy_definition_from_uploaded_bpmn(
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
                    source_bpmn_xml=source_bpmn_xml,
                    source_file_name=uploaded_file.filename,
                )
            except api.BpmnCoreError as exc:
                flash(str(exc), "error")
            else:
                flash(
                    f"Definition {deployment.definition.id} imported from uploaded "
                    f"BPMN file '{uploaded_file.filename}'.",
                    "success",
                )
                _flash_backend_catalog_result(deployment.backend_catalog)
            return redirect(url_for("process_definitions_page"))

    @app.post("/process-definitions/import-catalog")
    def import_catalog_definition_action():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            process_model_identifier = (
                request.form.get("process_model_identifier", "").strip()
            )
            bpmn_name = request.form.get("bpmn_name", "").strip() or None
            audit_context = current_app.extensions.get(
                SHARED_M8FLOW_AUDIT_CONTEXT_KEY
            )
            if not isinstance(audit_context, SharedM8flowAuditContext):
                flash("The local m8flow catalog is not available in this mode.", "error")
                return redirect(url_for("process_definitions_page"))

            try:
                deployment = deploy_definition_from_m8flow_catalog(
                    db_session,
                    tenant_id=identity.tenant.id,
                    tenant_slug=identity.tenant.slug,
                    user_id=identity.user.id,
                    audit_context=audit_context,
                    process_model_identifier=process_model_identifier,
                    bpmn_name=bpmn_name,
                )
            except api.BpmnCoreError as exc:
                flash(str(exc), "error")
            else:
                flash(
                    f"Definition {deployment.definition.id} imported from local "
                    f"m8flow catalog model '{process_model_identifier}'.",
                    "success",
                )
            return redirect(url_for("process_definitions_page"))

    @app.post("/process-definitions/deploy-timeout-escalation")
    def deploy_timeout_escalation_definition_action():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            process_model_identifier = (
                request.form.get("process_model_identifier", "").strip()
                or DEFAULT_TIMEOUT_ESCALATION_PROCESS_MODEL_IDENTIFIER
            )
            bpmn_name = (
                request.form.get("bpmn_name", "").strip()
                or DEFAULT_TIMEOUT_ESCALATION_BPMN_NAME
            )
            audit_context = current_app.extensions.get(
                SHARED_M8FLOW_AUDIT_CONTEXT_KEY
            )

            try:
                deployment = deploy_timeout_escalation_definition(
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
