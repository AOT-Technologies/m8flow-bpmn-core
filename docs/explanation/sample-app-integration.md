# About the sample app integration

The [sample app](../../sample_app/README.md) is a small Flask application that
uses m8flow-core the way a real host application would: as a wheel
dependency, with its own migrations and its own tables. This page explains
the decisions behind it, how it compares with m8flow, and what it found about
the library.

The sample app isn't a rebuild of m8flow. Its job is to prove that a thin
application can install the library, own its schema, and drive real
workflows end to end through the public API.

## Design decisions

The app is built on the following decisions:

- **The host application owns migrations.** The wheel doesn't ship Alembic
  revisions, so the sample app's own migration history creates the library's
  tables as well as its own. Its Alembic environment loads both
  `m8flow_core.models.Base` and the app's `SampleAppBase` metadata.
- **PostgreSQL by default.** The app defaults to the local m8flow database, so
  its data can be inspected and compared with m8flow.
- **Definitions live in the database.** Deployed BPMN goes into
  `bpmn_process_definition` through the library. There's no file-based
  template catalog.
- **App tables stay separate.** The app's `secret` table isn't part of the
  library's metadata, but it follows m8flow's secret schema: `id`,
  `m8f_tenant_id`, `key`, `value`, `user_id`, `created_at_in_seconds`, and
  `updated_at_in_seconds`.
- **Authorization stays in the library.** The app picks or signs in a user,
  but every workflow permission check runs through the library's
  tenant-aware checks.

## Standalone and shared modes

The app runs in one of two modes:

- **Standalone mode** uses a local tenant and user picker. It suits isolated
  runs.
- **Shared audit mode** runs against the database of a local m8flow stack, so
  you can inspect the app's workflows in the m8flow UI.

In shared mode, the app does the following at startup:

- Creates the sample tenants as organizations, and the sample users, in the
  shared Keycloak realm, and resets each demo user's password to match the
  username.
- Creates a public browser client in that realm for the app's sign-in
  callback.
- Changes each `m8flow_tenant.id` to the matching Keycloak organization ID,
  because the m8flow backend resolves the active tenant from that ID.
- Points each `user.service` and `user.service_id` at the shared realm issuer
  and the real Keycloak user ID.

Sign-in in shared mode is a real browser redirect to Keycloak. The app opens a
session only when the callback returns the user that was selected. If shared
mode is on and Keycloak setup fails, startup fails, instead of writing
local-only users that m8flow couldn't use.

When you deploy a definition in shared mode and its identifier has the form
`<group>/<model>`, the app also publishes the BPMN file, and any DMN files, to
the m8flow backend's process model catalog, so the model appears under
**Processes** in the m8flow UI.

## Comparison with the m8flow path

The following table compares the sample app with the m8flow backend, concern
by concern:

| Concern | Sample app behavior | Compared with m8flow |
|---|---|---|
| Definition deployment | Imports BPMN through `ImportBpmnProcessDefinitionCommand` | Same persistence model and tables that m8flow expects |
| Process start | Starts instances through `InitializeProcessInstanceFromDefinitionCommand` | Same start contract, with authorization inside the library |
| Task lifecycle | Lists, claims, and completes tasks through public commands and queries | Same user task lifecycle |
| Timers | Runs an inline loop that calls `run_due_scheduler_jobs`, including a boundary-timer escalation | Shows that a thin app can run timers itself, without a separate worker stack |
| Service tasks | Sends the outcome email through a BPMN service task that calls the connector proxy's SMTP command, with `M8FLOW_SECRET:<NAME>` parameters resolved from tenant secrets | Shows that a host app can use shared connector infrastructure while following m8flow's parameter contract |
| Metadata | Reads `process_instance_metadata` after each task | Same, including string values |
| Event history | Reads `process_instance_event` | Same audit trail |
| Secrets | Uses an app-owned `secret` table, seeded with SMTP settings per tenant | Shows host tables living next to the library schema |
| Sign-in | A tenant-first picker; in shared mode, a Keycloak redirect and callback | Close to m8flow's sign-in, but without its full sign-in and sign-out lifecycle |
| Process catalog | Deploys built-in fixtures, uploads BPMN files, and in shared mode imports models from the m8flow catalog | Simpler than m8flow's process model management, but covers real models created in the m8flow UI |

## Integration findings

Running the app outside the library's test harness found the following:

- **The public API covers the main lifecycle:** import, start, list pending
  tasks, claim, complete, list instances, and read metadata and events.
- **Two reads need direct ORM access:** listing a tenant's stored definitions,
  and loading one work item by ID for a task detail page.
- **Task payload values are strings.** The app accepts JSON, but the library
  stores each value as a string.
- **Service task behavior can be injected without changing the library.** The
  app wraps the SMTP connector with tenant secret resolution, and the library
  still runs the service task synchronously.
- **A shared database needs a separate Alembic version table.** The app uses
  `m8flow_sample_app_alembic_version`, so it doesn't collide with the
  backend's `alembic_version` row.
- **Migrations must adopt existing tables.** Against the shared database, the
  `secret` migration adopts an existing compatible table instead of creating
  it.
- **Cross-metadata foreign keys need explicit migrations.** When an app table
  references a library table from a separate SQLAlchemy metadata object, write
  the foreign key in the migration by hand. Autogenerate doesn't handle it.
- **Wheel filenames must keep their version.** uv rejects a renamed wheel,
  such as `m8flow_core.whl`. The staging script keeps the original name,
  updates `tool.uv.sources`, and refreshes the hash in `sample_app/uv.lock`.
- **Windows builds can hit a locked temporary file.** `uv build` can fail on a
  locked `uv-trampoline-*.exe`. The run script falls back to
  `python -m build --wheel --no-isolation`, retries `uv sync`, and, if the
  repository's `.venv` is locked by another process, syncs
  `sample_app/.venv` instead.

## Verification

The app was verified against a staged wheel, not an editable checkout. The
wheel was built with `python -m build --wheel --no-isolation`, and
`sample_app/tests/test_end_to_end.py` confirmed the following:

- The demo BPMN deploys through the library.
- Process instances start and complete through the web app.
- Tasks can be claimed and completed.
- Requests over 1000 go through Finance before the final review.
- An interrupting boundary timer cancels a waiting task and escalates it to
  the Supervisor lane through the app's scheduler loop.
- The final outcome is sent through the BPMN SMTP service task.
- Metadata and event history are stored in the library tables.
- Secret management works next to the workflow tables.

## Related pages

- [Sample app README](../../sample_app/README.md)
- [Sample app architecture](../../sample_app/docs/README.md)
- [Build and install the package](../how-to/build-and-install-the-package.md)
- [Limitations](../reference/limitations.md)
