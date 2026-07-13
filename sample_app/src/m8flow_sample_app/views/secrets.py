from __future__ import annotations

from html import escape

from flask import Flask, flash, redirect, request, url_for

from m8flow_bpmn_core.errors import BpmnCoreError
from m8flow_sample_app.auth import get_active_identity
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.secrets import (
    create_secret,
    delete_secret,
    get_secret,
    list_secrets,
    update_secret,
)
from m8flow_sample_app.ui import format_timestamp, post_button, render_page


def register_secret_routes(app: Flask) -> None:
    @app.get("/secrets")
    def secrets_page():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            secrets = list_secrets(db_session, tenant_id=identity.tenant.id)
            rows = "".join(
                f"""
<tr>
  <td>{item.secret.id}</td>
  <td>{escape(item.secret.key)}</td>
  <td>(stored)</td>
  <td>{escape((item.user.display_name or item.user.username) if item.user else '')}</td>
  <td>{format_timestamp(item.secret.updated_at_in_seconds)}</td>
  <td class="actions">
    <a href="{escape(url_for("edit_secret_page", secret_id=item.secret.id))}">Edit</a>
    {post_button(url_for("delete_secret_action", secret_id=item.secret.id), "Delete")}
  </td>
</tr>
"""
                for item in secrets
            )
            table_html = (
                f"""
<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>Key</th>
      <th>Value</th>
      <th>Updated by</th>
      <th>Updated</th>
      <th>Actions</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
"""
                if secrets
                else "<p>No secrets are stored for this tenant yet.</p>"
            )

            body = f"""
<p>This is an app-owned table shaped like m8flow's current <code>secret</code>
schema: tenant scoped, unique per tenant and key, and linked to the user who
last updated it.</p>
<p>Values are intentionally not shown in the list view, which matches the
current m8flow UI direction more closely.</p>
<p><a href="{escape(url_for("new_secret_page"))}">Create a new secret</a></p>
{table_html}
"""
            return render_page("Secrets", body, identity=identity)

    @app.route("/secrets/new", methods=["GET", "POST"])
    def new_secret_page():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            key_value = request.form.get("key", "")
            value_text = request.form.get("value", "")
            if request.method == "POST":
                try:
                    create_secret(
                        db_session,
                        tenant_id=identity.tenant.id,
                        user_id=identity.user.id,
                        key=key_value,
                        value=value_text,
                    )
                except BpmnCoreError as exc:
                    flash(str(exc), "error")
                else:
                    flash(f"Secret {key_value.strip()!r} created.", "success")
                    return redirect(url_for("secrets_page"))

            body = f"""
<form method="post">
  <label for="key">Secret key</label><br />
  <input id="key" name="key" type="text" value="{escape(key_value)}" /><br /><br />
  <label for="value">Secret value</label><br />
  <textarea id="value" name="value">{escape(value_text)}</textarea><br /><br />
  <button type="submit">Create secret</button>
</form>
"""
            return render_page("Create Secret", body, identity=identity)

    @app.route("/secrets/<int:secret_id>/edit", methods=["GET", "POST"])
    def edit_secret_page(secret_id: int):
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            try:
                secret = get_secret(
                    db_session,
                    tenant_id=identity.tenant.id,
                    secret_id=secret_id,
                )
            except BpmnCoreError as exc:
                flash(str(exc), "error")
                return redirect(url_for("secrets_page"))

            key_value = request.form.get("key", secret.key)
            value_text = request.form.get("value", "")
            if request.method == "POST":
                try:
                    update_secret(
                        db_session,
                        tenant_id=identity.tenant.id,
                        secret_id=secret_id,
                        user_id=identity.user.id,
                        key=key_value,
                        value=value_text if value_text.strip() else None,
                    )
                except BpmnCoreError as exc:
                    flash(str(exc), "error")
                else:
                    flash(f"Secret {key_value.strip()!r} updated.", "success")
                    return redirect(url_for("secrets_page"))

            body = f"""
<p>The current stored value is not shown. Provide a new value only when you want
to replace it.</p>
<form method="post">
  <label for="key">Secret key</label><br />
  <input id="key" name="key" type="text" value="{escape(key_value)}" /><br /><br />
  <label for="value">New secret value</label><br />
  <textarea id="value" name="value">{escape(value_text)}</textarea><br /><br />
  <button type="submit">Update secret</button>
</form>
"""
            return render_page(
                f"Edit Secret {secret.id}",
                body,
                identity=identity,
            )

    @app.post("/secrets/<int:secret_id>/delete")
    def delete_secret_action(secret_id: int):
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            try:
                delete_secret(
                    db_session,
                    tenant_id=identity.tenant.id,
                    secret_id=secret_id,
                )
            except BpmnCoreError as exc:
                flash(str(exc), "error")
            else:
                flash(f"Secret {secret_id} deleted.", "success")
            return redirect(url_for("secrets_page"))
