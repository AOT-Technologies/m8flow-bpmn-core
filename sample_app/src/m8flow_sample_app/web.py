from __future__ import annotations

from html import escape

from flask import Flask, redirect, request, url_for

from m8flow_sample_app.auth import (
    clear_active_identity,
    get_active_identity,
    list_tenants,
    list_users_for_tenant,
    set_active_identity,
)
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.ui import render_page
from m8flow_sample_app.views.process_definitions import (
    register_process_definition_routes,
)
from m8flow_sample_app.views.process_instances import (
    register_process_instance_routes,
)
from m8flow_sample_app.views.secrets import register_secret_routes
from m8flow_sample_app.views.tasks import register_task_routes


def register_web_routes(app: Flask) -> None:
    register_process_definition_routes(app)
    register_process_instance_routes(app)
    register_secret_routes(app)
    register_task_routes(app)

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get("/")
    def home():
        with session_scope() as db_session:
            identity = get_active_identity(db_session)
            if identity is None:
                return redirect(url_for("select_identity"))

            body = f"""
<form method="post" action="{escape(url_for("clear_identity"))}">
  <button type="submit">Change tenant or user</button>
</form>
<h2>Sections</h2>
<ul>
  <li><a href="{escape(url_for("process_definitions_page"))}">Process definitions</a></li>
  <li><a href="{escape(url_for("start_workflow"))}">Start workflow</a></li>
  <li><a href="{escape(url_for("tasks_page"))}">Tasks</a></li>
  <li><a href="{escape(url_for("process_instances_page"))}">Process instances</a></li>
  <li><a href="{escape(url_for("secrets_page"))}">Secrets</a></li>
</ul>
<p>This app now uses the library for definition import, process start, task
claim and completion, metadata persistence, and event listing.</p>
"""
            return render_page("m8flow-bpmn-core Sample App", body, identity=identity)

    @app.route("/session/select", methods=["GET", "POST"])
    def select_identity():
        with session_scope() as db_session:
            if request.method == "POST":
                tenant_id = request.form.get("tenant_id", "").strip()
                user_id_raw = request.form.get("user_id", "").strip()
                if tenant_id and user_id_raw.isdigit():
                    matching_user_ids = {
                        user.id
                        for user in list_users_for_tenant(
                            db_session,
                            tenant_id=tenant_id,
                        )
                    }
                    user_id = int(user_id_raw)
                    if user_id in matching_user_ids:
                        set_active_identity(tenant_id=tenant_id, user_id=user_id)
                        return redirect(url_for("home"))

            tenants = list_tenants(db_session)
            selected_tenant_id = request.args.get("tenant_id", "").strip()
            if not selected_tenant_id and tenants:
                selected_tenant_id = tenants[0].id

            users = (
                list_users_for_tenant(db_session, tenant_id=selected_tenant_id)
                if selected_tenant_id
                else []
            )

            tenant_options = "".join(
                f"<option value=\"{escape(tenant.id)}\""
                + (" selected" if tenant.id == selected_tenant_id else "")
                + f">{escape(tenant.name)} ({escape(tenant.slug)})</option>"
                for tenant in tenants
            )
            user_options = "".join(
                f"<option value=\"{user.id}\">{escape(user.display_name or user.username)}"
                f" [{escape(user.username)}]</option>"
                for user in users
            )
            user_select_html = (
                f"""
<form method="post">
  <input type="hidden" name="tenant_id" value="{escape(selected_tenant_id)}" />
  <label for="user_id">User</label><br />
  <select id="user_id" name="user_id">{user_options}</select><br /><br />
  <button type="submit">Enter app</button>
</form>
"""
                if users
                else "<p>No users are available for the selected tenant.</p>"
            )

            body = f"""
<p>Select a tenant and then choose one of its pre-seeded users.</p>
<form method="get">
  <label for="tenant_id">Tenant</label><br />
  <select id="tenant_id" name="tenant_id">{tenant_options}</select><br /><br />
  <button type="submit">Load users</button>
</form>
<hr />
{user_select_html}
"""
            return render_page("Select tenant and user", body)

    @app.post("/session/clear")
    def clear_identity():
        clear_active_identity()
        return redirect(url_for("select_identity"))
