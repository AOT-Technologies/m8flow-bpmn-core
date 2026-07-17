from __future__ import annotations

from html import escape

from flask import Flask, current_app, flash, redirect, request, url_for

from m8flow_sample_app.auth import (
    clear_active_identity,
    clear_pending_shared_login,
    find_user_for_service_identity,
    get_active_identity,
    get_pending_shared_login,
    list_tenants,
    list_users_for_tenant,
    set_pending_shared_login,
    set_active_identity,
)
from m8flow_sample_app.db import session_scope
from m8flow_sample_app.keycloak_login import (
    SHARED_KEYCLOAK_CALLBACK_PATH,
    build_shared_realm_authorization_url,
    create_pkce_code_verifier,
    exchange_shared_realm_authorization_code,
    KeycloakLoginError,
    pkce_code_challenge_for,
)
from m8flow_sample_app.settings import get_settings
from m8flow_sample_app.shared_m8flow import (
    SHARED_M8FLOW_AUDIT_CONTEXT_KEY,
    SharedM8flowAuditContext,
)
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
            audit_context = current_app.extensions.get(
                SHARED_M8FLOW_AUDIT_CONTEXT_KEY
            )
            shared_mode = (
                isinstance(audit_context, SharedM8flowAuditContext)
                and audit_context.uses_shared_m8flow
            )
            if request.method == "POST":
                tenant_id = request.form.get("tenant_id", "").strip()
                user_id_raw = request.form.get("user_id", "").strip()
                if tenant_id and user_id_raw.isdigit():
                    matching_users = list_users_for_tenant(
                        db_session,
                        tenant_id=tenant_id,
                    )
                    user_by_id = {user.id: user for user in matching_users}
                    matching_user_ids = set(user_by_id)
                    user_id = int(user_id_raw)
                    if user_id in matching_user_ids:
                        if shared_mode:
                            return redirect(
                                url_for(
                                    "start_shared_keycloak_login",
                                    tenant_id=tenant_id,
                                    user_id=user_id,
                                )
                            )
                        else:
                            set_active_identity(tenant_id=tenant_id, user_id=user_id)
                            return redirect(url_for("home"))

            tenants = list_tenants(db_session)
            selected_tenant_id = request.args.get("tenant_id", "").strip()
            if request.method == "POST" and not selected_tenant_id:
                selected_tenant_id = request.form.get("tenant_id", "").strip()
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
            shared_login_note = ""
            submit_label = "Enter app"
            user_form_action = ""
            if shared_mode:
                shared_login_note = (
                    "<p>Shared audit mode is active. This page authenticates "
                    "the selected tenant and user through a real Keycloak "
                    "browser redirect before opening the app session. The "
                    "selected user is sent as a login hint to Keycloak. Each "
                    "seeded shared-mode user uses the username itself as the "
                    "Keycloak password, for example "
                    "<code>alpha-admin</code> / <code>alpha-admin</code>.</p>"
                )
                submit_label = "Continue to Keycloak"
                user_form_action = (
                    f' action="{escape(url_for("start_shared_keycloak_login"))}"'
                )
            user_select_html = (
                f"""
<form method="post"{user_form_action}>
  <input type="hidden" name="tenant_id" value="{escape(selected_tenant_id)}" />
  <label for="user_id">User</label><br />
  <select id="user_id" name="user_id">{user_options}</select><br /><br />
  <button type="submit">{escape(submit_label)}</button>
</form>
"""
                if users
                else "<p>No users are available for the selected tenant.</p>"
            )

            body = f"""
<p>Select a tenant and then choose one of its pre-seeded users.</p>
{shared_login_note}
<form method="get">
  <label for="tenant_id">Tenant</label><br />
  <select
    id="tenant_id"
    name="tenant_id"
    onchange="this.form.requestSubmit()"
  >{tenant_options}</select><br /><br />
</form>
<hr />
{user_select_html}
"""
            return render_page("Select tenant and user", body)

    @app.route("/session/keycloak/start", methods=["GET", "POST"])
    def start_shared_keycloak_login():
        settings = get_settings()
        audit_context = current_app.extensions.get(SHARED_M8FLOW_AUDIT_CONTEXT_KEY)
        shared_mode = (
            isinstance(audit_context, SharedM8flowAuditContext)
            and audit_context.uses_shared_m8flow
        )
        if not shared_mode:
            flash("Shared Keycloak login is only available in shared audit mode.", "error")
            return redirect(url_for("select_identity"))

        tenant_id = request.values.get("tenant_id", "").strip()
        user_id_raw = request.values.get("user_id", "").strip()
        if not tenant_id or not user_id_raw.isdigit():
            flash("Select a tenant and user before continuing to Keycloak.", "error")
            return redirect(url_for("select_identity"))

        with session_scope() as db_session:
            matching_users = list_users_for_tenant(db_session, tenant_id=tenant_id)
            user_by_id = {user.id: user for user in matching_users}
            selected_user = user_by_id.get(int(user_id_raw))
            if selected_user is None:
                flash("The selected user is not available for this tenant.", "error")
                return redirect(url_for("select_identity", tenant_id=tenant_id))
            selected_user_id = selected_user.id
            selected_username = selected_user.username

        state = create_pkce_code_verifier()
        code_verifier = create_pkce_code_verifier()
        redirect_uri = url_for("shared_keycloak_callback", _external=True)
        set_pending_shared_login(
            tenant_id=tenant_id,
            expected_user_id=selected_user_id,
            state=state,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )
        authorization_url = build_shared_realm_authorization_url(
            client_id=settings.keycloak_login_client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=pkce_code_challenge_for(code_verifier),
            login_hint=selected_username,
            prompt="login",
        )
        return redirect(authorization_url)

    @app.get(SHARED_KEYCLOAK_CALLBACK_PATH)
    def shared_keycloak_callback():
        settings = get_settings()
        audit_context = current_app.extensions.get(SHARED_M8FLOW_AUDIT_CONTEXT_KEY)
        shared_mode = (
            isinstance(audit_context, SharedM8flowAuditContext)
            and audit_context.uses_shared_m8flow
        )
        if not shared_mode:
            clear_pending_shared_login()
            flash("Shared Keycloak callback is only available in shared audit mode.", "error")
            return redirect(url_for("select_identity"))

        pending_login = get_pending_shared_login()
        if pending_login is None:
            flash("The shared Keycloak login session has expired. Try again.", "error")
            return redirect(url_for("select_identity"))

        returned_state = request.args.get("state", "").strip()
        if returned_state != pending_login.state:
            clear_pending_shared_login()
            flash("The shared Keycloak login response could not be validated.", "error")
            return redirect(url_for("select_identity", tenant_id=pending_login.tenant_id))

        login_error = request.args.get("error", "").strip()
        if login_error:
            description = request.args.get("error_description", "").strip()
            clear_pending_shared_login()
            flash(description or login_error, "error")
            return redirect(url_for("select_identity", tenant_id=pending_login.tenant_id))

        code = request.args.get("code", "").strip()
        if not code:
            clear_pending_shared_login()
            flash("Keycloak did not return an authorization code.", "error")
            return redirect(url_for("select_identity", tenant_id=pending_login.tenant_id))

        try:
            authenticated_user = exchange_shared_realm_authorization_code(
                code=code,
                client_id=settings.keycloak_login_client_id,
                redirect_uri=pending_login.redirect_uri,
                code_verifier=pending_login.code_verifier,
            )
        except KeycloakLoginError as exc:
            clear_pending_shared_login()
            flash(str(exc), "error")
            return redirect(url_for("select_identity", tenant_id=pending_login.tenant_id))

        with session_scope() as db_session:
            resolved_user = find_user_for_service_identity(
                db_session,
                tenant_id=pending_login.tenant_id,
                service=authenticated_user.issuer,
                service_id=authenticated_user.subject,
            )
            if resolved_user is None:
                clear_pending_shared_login()
                flash(
                    "Keycloak login succeeded, but the sample app could not "
                    "find a matching user row for this tenant.",
                    "error",
                )
                return redirect(
                    url_for("select_identity", tenant_id=pending_login.tenant_id)
                )
            resolved_user_id = resolved_user.id
            resolved_username = resolved_user.username
            if resolved_user_id != pending_login.expected_user_id:
                clear_pending_shared_login()
                flash(
                    "Keycloak authenticated a different user than the one "
                    "selected on the tenant page.",
                    "error",
                )
                return redirect(
                    url_for("select_identity", tenant_id=pending_login.tenant_id)
                )

        set_active_identity(
            tenant_id=pending_login.tenant_id,
            user_id=resolved_user_id,
        )
        flash(
            "Shared Keycloak login succeeded for "
            f"{resolved_username}.",
            "success",
        )
        return redirect(url_for("home"))

    @app.post("/session/clear")
    def clear_identity():
        clear_active_identity()
        return redirect(url_for("select_identity"))
