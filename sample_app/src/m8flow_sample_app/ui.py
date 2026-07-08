from __future__ import annotations

from html import escape

from flask import current_app, get_flashed_messages, url_for

from m8flow_sample_app.auth import ActiveIdentity
from m8flow_sample_app.shared_m8flow import (
    SHARED_M8FLOW_AUDIT_CONTEXT_KEY,
    SharedM8flowAuditContext,
)


def render_page(
    title: str,
    body: str,
    *,
    identity: ActiveIdentity | None = None,
) -> str:
    nav_html = _navigation(identity)
    audit_html = _audit_mode_banner(identity)
    flash_html = _flash_messages()

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)}</title>
    <style>
      body {{
        font-family: sans-serif;
        margin: 2rem auto;
        max-width: 1100px;
        padding: 0 1rem 3rem;
      }}
      nav a {{
        margin-right: 1rem;
      }}
      table {{
        border-collapse: collapse;
        width: 100%;
      }}
      th, td {{
        border: 1px solid #ccc;
        padding: 0.45rem 0.6rem;
        text-align: left;
        vertical-align: top;
      }}
      textarea {{
        width: 100%;
        min-height: 12rem;
        font-family: monospace;
      }}
      input[type="text"], select {{
        min-width: 22rem;
        max-width: 100%;
      }}
      .flash-success {{
        background: #edf7ed;
        border: 1px solid #7fb77e;
        padding: 0.75rem;
      }}
      .flash-error {{
        background: #fdecea;
        border: 1px solid #d67c73;
        padding: 0.75rem;
      }}
      .flash-warning {{
        background: #fff7e6;
        border: 1px solid #e0b15b;
        padding: 0.75rem;
      }}
      .audit-banner {{
        margin: 1rem 0;
        padding: 0.85rem 1rem;
        border: 1px solid #cbd5e1;
        background: #f8fafc;
      }}
      .audit-banner h2 {{
        margin: 0 0 0.4rem;
        font-size: 1rem;
      }}
      .audit-banner ul {{
        margin: 0.35rem 0 0 1.25rem;
        padding: 0;
      }}
      .audit-shared {{
        border-color: #7fb77e;
        background: #edf7ed;
      }}
      .audit-standalone {{
        border-color: #94a3b8;
        background: #f8fafc;
      }}
      .identity {{
        margin: 0.75rem 0 1rem;
        color: #444;
      }}
      .actions form {{
        display: inline-block;
        margin: 0 0.4rem 0.4rem 0;
      }}
      code {{
        white-space: pre-wrap;
      }}
    </style>
  </head>
  <body>
    <h1>{escape(title)}</h1>
    {nav_html}
    {audit_html}
    {flash_html}
    {body}
  </body>
</html>
"""


def post_button(
    action: str,
    label: str,
    *,
    hidden_fields: dict[str, str] | None = None,
) -> str:
    hidden_inputs = ""
    for key, value in (hidden_fields or {}).items():
        hidden_inputs += (
            f'<input type="hidden" name="{escape(key)}" value="{escape(value)}" />'
        )
    return (
        f'<form method="post" action="{escape(action)}">'
        f"{hidden_inputs}<button type=\"submit\">{escape(label)}</button></form>"
    )


def _navigation(identity: ActiveIdentity | None) -> str:
    links = [f'<a href="{escape(url_for("home"))}">Home</a>']
    if identity is None:
        links.append(
            f'<a href="{escape(url_for("select_identity"))}">Select tenant and user</a>'
        )
        return f"<nav>{''.join(links)}</nav>"

    links.extend(
        [
            f'<a href="{escape(url_for("process_definitions_page"))}">Process definitions</a>',
            f'<a href="{escape(url_for("start_workflow"))}">Start workflow</a>',
            f'<a href="{escape(url_for("tasks_page"))}">Tasks</a>',
            f'<a href="{escape(url_for("process_instances_page"))}">Process instances</a>',
            f'<a href="{escape(url_for("secrets_page"))}">Secrets</a>',
        ]
    )
    identity_summary = (
        f"<div class=\"identity\"><strong>Tenant:</strong> "
        f"{escape(identity.tenant.name)} ({escape(identity.tenant.id)})"
        f" | <strong>User:</strong> "
        f"{escape(identity.user.display_name or identity.user.username)} "
        f"({escape(identity.user.username)})</div>"
    )
    return f"<nav>{''.join(links)}</nav>{identity_summary}"


def _flash_messages() -> str:
    messages = get_flashed_messages(with_categories=True)
    if not messages:
        return ""

    items = []
    for category, message in messages:
        safe_category = escape(category or "info")
        safe_message = escape(str(message))
        items.append(f'<div class="flash-{safe_category}">{safe_message}</div>')
    return "".join(items)


def _audit_mode_banner(identity: ActiveIdentity | None) -> str:
    audit_context = current_app.extensions.get(SHARED_M8FLOW_AUDIT_CONTEXT_KEY)
    if not isinstance(audit_context, SharedM8flowAuditContext):
        return ""

    details: list[str] = []
    if audit_context.uses_shared_m8flow:
        banner_class = "audit-banner audit-shared"
        title = "Shared m8flow Audit Mode"
        details.append(
            "This sample app is using the shared m8flow-compatible database "
            "and shared Keycloak-backed user identities."
        )
        if identity is not None:
            details.append(
                "Current tenant slug "
                f"'{identity.tenant.slug}' maps to tenant id "
                f"'{identity.tenant.id}'."
            )
            details.append(
                "Current user is backed by service "
                f"'{identity.user.service}' with service id "
                f"'{identity.user.service_id}'."
            )
        if audit_context.process_models_root is not None:
            details.append(
                "Local m8flow backend process-model catalog: "
                f"{audit_context.process_models_root}"
            )
        else:
            details.append(
                "No local m8flow backend process-model catalog was discovered, "
                "so deployed BPMN files cannot yet appear under Processes in "
                "the m8flow UI."
            )
        if audit_context.backend_container_name:
            details.append(
                "Catalog discovery source: "
                f"{audit_context.backend_container_name}"
            )
    else:
        banner_class = "audit-banner audit-standalone"
        title = "Standalone Sample-App Mode"
        details.append(
            "This sample app is running without shared m8flow audit mode. "
            "Workflow execution works normally, but m8flow UI will not reuse "
            "these local-only identities or process-model catalog files."
        )

    details.extend(audit_context.warnings)
    items = "".join(f"<li>{escape(item)}</li>" for item in details)
    return (
        f'<section class="{escape(banner_class)}">'
        f"<h2>{escape(title)}</h2><ul>{items}</ul></section>"
    )
