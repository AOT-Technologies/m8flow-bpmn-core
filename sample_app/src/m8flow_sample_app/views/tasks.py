from __future__ import annotations

from html import escape

from flask import Flask, flash, redirect, request, url_for

from m8flow_bpmn_core import api
from m8flow_sample_app.auth import get_active_identity
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.ui import post_button, render_page
from m8flow_sample_app.workflows.tasks import (
    build_task_payload_from_json_text,
    claim_task,
    get_accessible_task,
    list_pending_tasks,
    complete_task,
)


JSON_TEXT_EXAMPLE = """{
  "note": "Completed from the sample app",
  "approved": true
}"""


def register_task_routes(app: Flask) -> None:
    @app.get("/tasks")
    def tasks_page():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            tasks = list_pending_tasks(
                db_session,
                tenant_id=identity.tenant.id,
                user_id=identity.user.id,
            )
            rows = []
            for human_task in tasks:
                actions = []
                if human_task.actual_owner_id is None:
                    actions.append(
                        post_button(
                            url_for("claim_task_action", human_task_id=human_task.id),
                            "Claim",
                        )
                    )
                actions.append(
                    f'<a href="{escape(url_for("task_detail", human_task_id=human_task.id))}">Open</a>'
                )
                rows.append(
                    f"""
<tr>
  <td>{human_task.id}</td>
  <td>{escape(human_task.task_title or human_task.task_name)}</td>
  <td>{escape(human_task.task_status)}</td>
  <td>{escape(human_task.lane_name or '')}</td>
  <td>{human_task.process_instance_id}</td>
  <td class="actions">{''.join(actions)}</td>
</tr>
"""
                )
            tasks_html = (
                f"""
<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>Task</th>
      <th>Status</th>
      <th>Lane</th>
      <th>Process instance</th>
      <th>Actions</th>
    </tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table>
"""
                if tasks
                else "<p>No pending tasks are currently assigned to this user.</p>"
            )
            body = f"""
<p>This screen uses <code>GetPendingTasksQuery</code> scoped to the active
tenant and user.</p>
{tasks_html}
"""
            return render_page("Tasks", body, identity=identity)

    @app.post("/tasks/<int:human_task_id>/claim")
    def claim_task_action(human_task_id: int):
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))
            try:
                claim_task(
                    db_session,
                    tenant_id=identity.tenant.id,
                    user_id=identity.user.id,
                    human_task_id=human_task_id,
                )
            except api.BpmnCoreError as exc:
                flash(str(exc), "error")
            else:
                flash(f"Task {human_task_id} claimed.", "success")
            return redirect(url_for("tasks_page"))

    @app.get("/tasks/<int:human_task_id>")
    def task_detail(human_task_id: int):
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            try:
                task = get_accessible_task(
                    db_session,
                    tenant_id=identity.tenant.id,
                    user_id=identity.user.id,
                    human_task_id=human_task_id,
                )
            except api.BpmnCoreError as exc:
                flash(str(exc), "error")
                return redirect(url_for("tasks_page"))
            can_complete = (
                task.actual_owner_id == identity.user.id
                and task.completed is False
                and task.task_status == "CLAIMED"
            )
            claim_html = (
                post_button(
                    url_for("claim_task_action", human_task_id=task.id),
                    "Claim this task",
                )
                if task.actual_owner_id is None and not task.completed
                else ""
            )
            complete_html = (
                f"""
<form method="post" action="{escape(url_for("complete_task_action", human_task_id=task.id))}">
  <label for="task_payload_json">Task payload JSON</label><br />
  <textarea id="task_payload_json" name="task_payload_json">{escape(JSON_TEXT_EXAMPLE)}</textarea><br /><br />
  <button type="submit">Submit task payload and complete</button>
</form>
<p>All JSON values are persisted as strings through the library metadata API.</p>
"""
                if can_complete
                else "<p>Claim this task before submitting a payload.</p>"
            )
            body = f"""
<p><strong>Task:</strong> {escape(task.task_title or task.task_name)}</p>
<p><strong>Status:</strong> {escape(task.task_status)}</p>
<p><strong>Lane:</strong> {escape(task.lane_name or '')}</p>
<p><strong>Process instance:</strong>
  <a href="{escape(url_for("process_instance_detail", process_instance_id=task.process_instance_id))}">
    {task.process_instance_id}
  </a>
</p>
<p><strong>Actual owner ID:</strong> {task.actual_owner_id or ''}</p>
{claim_html}
{complete_html}
"""
            return render_page(f"Task {task.id}", body, identity=identity)

    @app.post("/tasks/<int:human_task_id>/complete")
    def complete_task_action(human_task_id: int):
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            payload_text = request.form.get("task_payload_json", "")
            try:
                payload = build_task_payload_from_json_text(payload_text)
                complete_task(
                    db_session,
                    tenant_id=identity.tenant.id,
                    user_id=identity.user.id,
                    human_task_id=human_task_id,
                    task_payload=payload,
                )
            except (ValueError, api.BpmnCoreError) as exc:
                flash(str(exc), "error")
                return redirect(url_for("task_detail", human_task_id=human_task_id))

            flash(f"Task {human_task_id} completed.", "success")
            return redirect(url_for("tasks_page"))
