from __future__ import annotations

from html import escape

from flask import get_flashed_messages, url_for

from m8flow_sample_app.auth import ActiveIdentity


def render_page(
    title: str,
    body: str,
    *,
    identity: ActiveIdentity | None = None,
) -> str:
    nav_html = _navigation(identity)
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
