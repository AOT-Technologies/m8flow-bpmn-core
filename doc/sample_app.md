# Sample App Integration Plan

This document tracks the thin host-application demo requested for validating
`m8flow-bpmn-core` as a real dependency.

The goal is not to rebuild m8flow. The goal is to prove that a small web app
can install the library from a wheel, own its own migrations, and drive one
workflow end to end through the public Python API.

## Agreed Constraints

- The host app owns migrations. The library wheel will not package Alembic
  migration files.
- The host app should default to PostgreSQL so the integration is easy to
  audit and compare with m8flow.
- Authentication is mocked through a tenant and user picker, but workflow
  authorization still runs through the library's tenant-aware RBAC checks.
- Process definitions are stored in the database through
  `bpmn_process_definition`; the sample app does not need a filesystem template
  catalog.
- App-owned tables such as secrets should stay separate from library-owned
  workflow tables, but should follow the same general schema style used by
  m8flow.

## Step 1

Create the host-app scaffold, wheel-consumption path, and host-owned migration
layout.

Files:

- `sample_app/pyproject.toml`
  - Define the sample app package and runtime dependencies.
  - Consume `m8flow-bpmn-core` through a staged local wheel file.
- `sample_app/README.md`
  - Explain the sample app purpose, default PostgreSQL setup, and wheel staging
    flow.
- `sample_app/scripts/stage_local_wheel.ps1`
  - Copy the newest built wheel from `../dist/` into a stable vendor path that
    the sample app can depend on.
- `sample_app/scripts/stage_local_wheel.sh`
  - Unix equivalent of the wheel-staging helper.
- `sample_app/vendor/README.md`
  - Document the staged wheel location and why the wheel itself is not checked
    in.
- `sample_app/alembic.ini`
  - Host-app Alembic entrypoint.
- `sample_app/alembic/env.py`
  - Configure Alembic against both the library metadata and the sample app
    metadata.
- `sample_app/alembic/script.py.mako`
  - Standard migration template for the sample app.
- `sample_app/alembic/versions/README.md`
  - Placeholder for host-owned migration revisions.
- `sample_app/src/m8flow_sample_app/__init__.py`
  - Package marker.
- `sample_app/src/m8flow_sample_app/settings.py`
  - Postgres-default app settings.
- `sample_app/src/m8flow_sample_app/db.py`
  - Engine, session factory, session scope helpers, and programmatic Alembic
    startup migration runner.
- `sample_app/src/m8flow_sample_app/models.py`
  - Sample-app declarative base and combined metadata list for Alembic.

## Step 2

Add the runtime shell, startup flow, and seeded mocked identities.

Files:

- `sample_app/src/m8flow_sample_app/app.py`
  - Flask app factory and startup hooks.
- `sample_app/src/m8flow_sample_app/__main__.py`
  - Local runnable entrypoint.
- `sample_app/src/m8flow_sample_app/web.py`
  - Route registration and inline HTML pages for the current simple UI.
- `sample_app/src/m8flow_sample_app/auth.py`
  - Tenant and user session selection helpers.
- `sample_app/src/m8flow_sample_app/seed.py`
  - Seed tenants, users, groups, and V1 command permissions through the
    library's authorization helpers.
- `sample_app/alembic/versions/20260706_0001_create_library_baseline.py`
  - First host-owned migration that creates the library-compatible workflow
    tables plus initial app tables required for startup.

## Step 3

Add the workflow-facing web screens backed by the library.

Files:

- `sample_app/fixtures/sample_app_demo.bpmn`
  - Built-in two-step user-task workflow used by the sample app.
- `sample_app/src/m8flow_sample_app/ui.py`
  - Shared page layout, navigation, flash rendering, and simple post-button
    helper.
- `sample_app/src/m8flow_sample_app/views/process_definitions.py`
  - List stored definitions and deploy the built-in BPMN into the DB.
- `sample_app/src/m8flow_sample_app/views/process_instances.py`
  - Start workflows, list instances, and show instance detail plus
    metadata/events.
- `sample_app/src/m8flow_sample_app/views/tasks.py`
  - Pending-task list, claim action, task detail, and JSON completion form.
- `sample_app/src/m8flow_sample_app/workflows/deploy.py`
  - Use `ImportBpmnProcessDefinitionCommand` and tenant-specific lane owners.
- `sample_app/src/m8flow_sample_app/workflows/start.py`
  - Use `InitializeProcessInstanceFromDefinitionCommand`.
- `sample_app/src/m8flow_sample_app/workflows/tasks.py`
  - Use `GetPendingTasksQuery`, `ClaimTaskCommand`, and
    `CompleteTaskCommand`.
- `sample_app/src/m8flow_sample_app/workflows/instances.py`
  - Use `GetProcessInstanceQuery`, `GetProcessInstanceEventsQuery`,
    `GetProcessInstanceMetadataQuery`, and `ListProcessInstancesQuery`.

## Step 4

Add app-owned secrets CRUD using the same SQLAlchemy and Alembic patterns.

Files:

- `sample_app/src/m8flow_sample_app/models.py`
  - Add the `secret` ORM model aligned to m8flow's current secret schema.
- `sample_app/src/m8flow_sample_app/secrets.py`
  - CRUD helpers for tenant-scoped secrets.
- `sample_app/src/m8flow_sample_app/views/secrets.py`
  - Create, list, edit, and delete secrets.
- `sample_app/alembic/versions/20260706_0002_add_secret_table.py`
  - Host-owned schema change for secrets.

## Step 5

Add end-to-end tests and finalize the run documentation.

Files:

- `sample_app/tests/test_end_to_end.py`
  - Integration smoke test for process start, claim, complete, metadata, and
    events.
- `doc/sample_app.md`
  - Finalized runbook and documented integration findings.
- `doc/examples.md`
  - Link to the sample app once the workflow is runnable.

## Step 6

Verify the staged-wheel integration path and fix host-app runtime issues found
while running the sample app outside the library test harness.

Files:

- `sample_app/src/m8flow_sample_app/seed.py`
  - Avoid import-time circular dependencies while still using the library's
    authorization helpers for seeding.
- `sample_app/src/m8flow_sample_app/models.py`
  - Keep the app-owned `secret` model separate from the library metadata while
    preserving the m8flow-style column layout.
- `sample_app/src/m8flow_sample_app/secrets.py`
  - Join secret rows to users explicitly and harden duplicate-key rollback
    behavior.
- `sample_app/src/m8flow_sample_app/views/secrets.py`
  - Render the joined secret and owner data returned by the updated helpers.
- `sample_app/alembic/versions/20260706_0002_add_secret_table.py`
  - Create the app-owned `secret` table explicitly so host-owned migrations can
    safely reference library-owned tables.
- `sample_app/alembic.ini`
  - Silence the Alembic path-separator warning during startup and test runs.

## Step 7

Document the validated behavior, the comparison to the current m8flow-style
workflow path, and the remaining host-app integration gaps.

Files:

- `doc/sample_app.md`
  - Record the behavior comparison and integration issue log.
- `sample_app/README.md`
  - Update the sample-app status and point readers to the comparison notes.
- `doc/examples.md`
  - Keep the sample-app entry aligned with the finalized runbook.

## Runbook

1. Build the library wheel from the repo root:
   - Preferred: `uv build --wheel`
   - Windows fallback if `uv build` fails with a `uv-trampoline-*.exe`
     temporary-file lock error:
     - `python -m pip install build hatchling`
     - `python -m build --wheel --no-isolation`
2. Stage the newest wheel into the sample app vendor path:
   - PowerShell: `.\sample_app\scripts\stage_local_wheel.ps1`
   - Bash: `bash sample_app/scripts/stage_local_wheel.sh`
3. Sync the sample app environment:
   - `cd sample_app`
   - `uv sync`
   - If you are already inside the repo root `.venv`, use `uv sync --active`
     instead so uv targets the active environment instead of creating
     `sample_app/.venv`
4. Start the app:
   - `uv run m8flow-sample-app`
   - If you are already inside the repo root `.venv`, use
     `uv run --active m8flow-sample-app`
5. Open `http://127.0.0.1:5010/session/select`
6. Sign in as `Sample Tenant Alpha / alpha-admin`
7. Deploy the built-in demo workflow from `Process definitions`
8. Start a workflow from `Start workflow`
9. Switch to `alpha-operator` and complete `Prepare Request`
10. Switch to `alpha-reviewer` and complete `Review Request`
11. Inspect the completed instance, metadata, and events from `Process instances`
12. Use `Secrets` to verify the app-owned secret CRUD flow

## Integration Findings

- The host app owns migrations. The library wheel does not package Alembic
  revisions, so the sample app migration history creates the library tables it
  depends on.
- When the sample app reuses the shared m8flow PostgreSQL database, it must
  keep its own Alembic version table. The sample app now uses
  `m8flow_sample_app_alembic_version`, so it does not collide with the
  backend's `alembic_version` row or try to resolve backend-only revision ids.
- The staged wheel must keep its versioned filename. `uv` rejects a renamed
  file such as `m8flow_bpmn_core.whl` because wheel filenames are required to
  include a normalized version. The staging helper now preserves the original
  wheel filename and rewrites `tool.uv.sources` automatically.
- On Windows, `uv build` can fail before packaging starts if its temporary
  trampoline executable is still locked by another process. That is an
  environment/tooling issue rather than a package metadata issue. For the
  sample-app flow, a safe fallback is:
  - `python -m pip install build hatchling`
  - `python -m build --wheel --no-isolation`
- The public API is sufficient for the main workflow lifecycle:
  - import definition
  - start process
  - list pending tasks
  - claim task
  - complete task
  - list instances
  - read metadata
  - read events
- Two read-side gaps still require direct ORM access in the host app:
  - listing stored process definitions by tenant
  - loading a single human task by id for the task-detail page
- `CompleteTaskCommand.task_payload` persists metadata values as strings. The
  sample app accepts JSON input for convenience, but stored metadata values are
  stringified by the library.
- The app-owned `secret` table was kept separate from the library and aligned
  to the current m8flow schema shape:
  - `id`
  - `m8f_tenant_id`
  - `key`
  - `value`
  - `user_id`
  - `created_at_in_seconds`
  - `updated_at_in_seconds`
- When the sample app runs against the shared m8flow PostgreSQL database, its
  `secret` migration adopts an existing compatible `secret` table instead of
  trying to recreate it.

## Behavior Comparison With The Current m8flow-Style Path

The sample app does not try to recreate the full m8flow backend or UI. It
proves that the workflow behavior m8flow depends on can be driven from a thin
host application through the public library API.

| Concern | Sample app behavior | Comparison |
| --- | --- | --- |
| Definition deployment | Imports BPMN into `bpmn_process_definition` through `ImportBpmnProcessDefinitionCommand`. | Matches the library-backed persistence model and keeps the workflow in the same DB tables m8flow expects. |
| Process start | Starts instances through `InitializeProcessInstanceFromDefinitionCommand`. | Matches the in-process start contract and keeps tenant/user authorization inside the library. |
| Task lifecycle | Lists pending work, claims tasks, and completes tasks through the public commands and queries. | Matches the expected user-task lifecycle used by m8flow-style host apps. |
| Metadata persistence | Reads `process_instance_metadata` after each task submission. | Matches current library behavior, including stringified metadata values. |
| Event history | Reads `process_instance_event` for created, completed, and process-completed events. | Matches the auditable event trail required for host-app inspection. |
| Secrets | Uses an app-owned `secret` table outside the library metadata. | Demonstrates that host-owned tables can coexist with the library schema while following the same schema style used by m8flow. |
| Authentication UX | Uses a static tenant/user picker rather than Keycloak or the m8flow login flow. | Intentionally simpler than m8flow, but authorization still runs through the library's tenant-aware checks after identity selection. |
| Process catalog UX | Deploys a built-in BPMN fixture from the app instead of browsing a model catalog. | Simpler than m8flow's full process-model management, but enough to prove import, start, and execution end to end. |

## Documented Host-App Integration Issues

- The host app must own migrations for both library-owned and app-owned tables.
  The wheel does not ship Alembic revisions.
- Two read-side use cases still rely on direct ORM access in the sample app:
  - listing stored process definitions by tenant
  - loading a single human task by id for the task detail page
- `CompleteTaskCommand.task_payload` persists metadata values as strings, so a
  host app that accepts JSON input should expect stringified stored values.
- When an app-owned table references library-owned tables from a separate
  SQLAlchemy metadata object, the host migration should create those foreign
  keys explicitly instead of relying on cross-metadata autogeneration.

## Verification Snapshot

The finalized sample app was verified against a real staged wheel, not only an
editable source checkout.

- Wheel build: `python -m build --wheel --no-isolation`
- Sample-app smoke tests: `pytest tests/test_end_to_end.py -q`
- Verified outcomes:
  - demo BPMN deploys through the library
  - process instances start and complete through the web app
  - manual tasks can be claimed and completed through the library
  - process metadata and event history persist in the library tables
  - app-owned secrets CRUD works alongside the workflow tables
