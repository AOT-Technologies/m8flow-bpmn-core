from __future__ import annotations

from html import escape

from flask import Flask, flash, redirect, request, url_for

from m8flow_bpmn_core import api
from m8flow_sample_app.auth import get_active_identity
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.ui import format_timestamp, render_page
from m8flow_sample_app.workflows.deploy import (
    list_latest_process_definitions,
    list_process_definitions,
)
from m8flow_sample_app.workflows.instances import (
    get_process_instance_detail,
    list_process_instances,
)
from m8flow_sample_app.workflows.start import start_process_instance


def register_process_instance_routes(app: Flask) -> None:
    @app.route("/process-instances/start", methods=["GET", "POST"])
    def start_workflow():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            all_definitions = list_process_definitions(
                db_session,
                tenant_id=identity.tenant.id,
            )
            definitions = [
                definition
                for definition in list_latest_process_definitions(
                    db_session,
                    tenant_id=identity.tenant.id,
                )
            ]
            startable_definition_ids = {definition.id for definition in definitions}
            latest_definition_by_identifier = {
                definition.process_model_identifier: definition
                for definition in definitions
            }

            if request.method == "POST":
                definition_id_raw = request.form.get("definition_id", "").strip()
                summary = request.form.get("summary", "").strip() or None
                if definition_id_raw.isdigit():
                    definition_id = int(definition_id_raw)
                    if definition_id not in startable_definition_ids:
                        flash(
                            "Only the latest stored definition for each "
                            "process model can be started. Choose the newest "
                            "definition.",
                            "error",
                        )
                    else:
                        try:
                            process_instance = start_process_instance(
                                db_session,
                                tenant_id=identity.tenant.id,
                                user_id=identity.user.id,
                                definition_id=definition_id,
                                summary=summary,
                            )
                        except api.BpmnCoreError as exc:
                            flash(str(exc), "error")
                        else:
                            flash(
                                f"Started process instance {process_instance.id}.",
                                "success",
                            )
                            return redirect(
                                url_for(
                                    "process_instance_detail",
                                    process_instance_id=process_instance.id,
                                )
                            )
                else:
                    flash("Select a valid definition before starting a workflow.", "error")

            selected_definition_id = request.args.get("definition_id", "").strip()
            if selected_definition_id and selected_definition_id.isdigit():
                selected_historical_definition = next(
                    (
                        definition
                        for definition in all_definitions
                        if definition.id == int(selected_definition_id)
                    ),
                    None,
                )
                if selected_historical_definition is not None:
                    selected_definition_id = str(
                        latest_definition_by_identifier[
                            selected_historical_definition.process_model_identifier
                        ].id
                    )
            if definitions:
                definition_options = "".join(
                    f"<option value=\"{definition.id}\""
                    + (
                        " selected"
                        if selected_definition_id == str(definition.id)
                        else ""
                    )
                    + f">{definition.id} - {escape(definition.process_model_identifier)}"
                    + f" ({escape(definition.bpmn_name or definition.bpmn_identifier)})</option>"
                    for definition in definitions
                )
                form_html = f"""
<form method="post">
  <label for="definition_id">Stored definition</label><br />
  <select id="definition_id" name="definition_id">{definition_options}</select><br /><br />
  <label for="summary">Summary</label><br />
  <input
    id="summary"
    name="summary"
    type="text"
    value="{escape(f'Started by {identity.user.username} from the sample app')}"
  /><br /><br />
  <button type="submit">Start workflow</button>
</form>
"""
            else:
                form_html = (
                    "<p>No stored definitions are available yet. Deploy the demo workflow first.</p>"
                )

            body = f"""
<p>This screen starts a workflow through
<code>InitializeProcessInstanceFromDefinitionCommand</code>.</p>
{form_html}
"""
            return render_page("Start Workflow", body, identity=identity)

    @app.get("/process-instances")
    def process_instances_page():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            process_instances = list_process_instances(
                db_session,
                tenant_id=identity.tenant.id,
            )
            rows = "".join(
                f"""
<tr>
  <td>{process_instance.id}</td>
  <td>{escape(process_instance.process_model_display_name)}</td>
  <td>{escape(process_instance.status)}</td>
  <td>{escape(process_instance.process_initiator.username)}</td>
  <td>{format_timestamp(process_instance.start_in_seconds)}</td>
  <td><a href="{escape(url_for("process_instance_detail", process_instance_id=process_instance.id))}">Open</a></td>
</tr>
"""
                for process_instance in process_instances
            )
            instances_html = (
                f"""
<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>Name</th>
      <th>Status</th>
      <th>Started by</th>
      <th>Started</th>
      <th>Action</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
"""
                if process_instances
                else "<p>No process instances exist for this tenant yet.</p>"
            )
            body = f"""
<p>Instances below are loaded through <code>ListProcessInstancesQuery</code>.</p>
{instances_html}
"""
            return render_page("Process Instances", body, identity=identity)

    @app.get("/process-instances/<int:process_instance_id>")
    def process_instance_detail(process_instance_id: int):
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            try:
                detail = get_process_instance_detail(
                    db_session,
                    tenant_id=identity.tenant.id,
                    process_instance_id=process_instance_id,
                )
            except api.BpmnCoreError as exc:
                flash(str(exc), "error")
                return redirect(url_for("process_instances_page"))

            metadata_rows = "".join(
                f"<tr><td>{escape(item.key)}</td><td>{escape(item.value)}</td></tr>"
                for item in detail.metadata
            )
            metadata_html = (
                f"""
<table>
  <thead><tr><th>Key</th><th>Value</th></tr></thead>
  <tbody>{metadata_rows}</tbody>
</table>
"""
                if detail.metadata
                else "<p>No metadata has been stored for this instance yet.</p>"
            )

            event_rows = "".join(
                f"""
<tr>
  <td>{event.id}</td>
  <td>{escape(event.event_type)}</td>
  <td>{escape(event.task_guid or '')}</td>
  <td>{escape((event.user.display_name or event.user.username) if event.user else '')}</td>
  <td>{format_timestamp(event.timestamp)}</td>
</tr>
"""
                for event in detail.events
            )
            events_html = (
                f"""
<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>Event type</th>
      <th>Task guid</th>
      <th>User</th>
      <th>Timestamp</th>
    </tr>
  </thead>
  <tbody>{event_rows}</tbody>
</table>
"""
                if detail.events
                else "<p>No events have been recorded for this instance yet.</p>"
            )

            human_task_rows = "".join(
                f"""
<tr>
  <td>{human_task.id}</td>
  <td>{escape(human_task.task_title or human_task.task_name)}</td>
  <td>{escape(human_task.task_status)}</td>
  <td>{escape(human_task.lane_name or '')}</td>
  <td>{human_task.actual_owner_id or ''}</td>
  <td>{
      (
          f'<a href="{escape(url_for("task_detail", human_task_id=human_task.id))}">Open task</a>'
          if any(owner.id == identity.user.id for owner in human_task.potential_owners)
          else "Not assigned to active user"
      )
  }</td>
</tr>
"""
                for human_task in detail.human_tasks
            )
            human_tasks_html = (
                f"""
<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>Task</th>
      <th>Status</th>
      <th>Lane</th>
      <th>Owner ID</th>
      <th>Action</th>
    </tr>
  </thead>
  <tbody>{human_task_rows}</tbody>
</table>
"""
                if detail.human_tasks
                else "<p>No human tasks are attached to this instance.</p>"
            )

            body = f"""
<p><strong>Status:</strong> {escape(detail.process_instance.status)}</p>
<p><strong>Summary:</strong> {escape(detail.process_instance.summary or '')}</p>
<p><strong>Process model identifier:</strong> {escape(detail.process_instance.process_model_identifier)}</p>
<p><strong>Started by:</strong> {escape(detail.process_instance.process_initiator.username)}</p>
<p><strong>Started:</strong> {format_timestamp(detail.process_instance.start_in_seconds)}</p>
<p><strong>Ended:</strong> {format_timestamp(detail.process_instance.end_in_seconds)}</p>
<h2>Human tasks</h2>
{human_tasks_html}
<h2>Metadata</h2>
{metadata_html}
<h2>Events</h2>
{events_html}
"""
            return render_page(
                f"Process Instance {process_instance_id}",
                body,
                identity=identity,
            )
