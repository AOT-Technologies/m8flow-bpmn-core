# Sample App

This folder will host the thin web app used to validate
`m8flow-bpmn-core` as a real dependency.

The sample app owns its own Alembic migrations. That matches the agreed
direction for this ticket: the host application is responsible for maintaining
the schema it needs, even for library-owned workflow tables.

When the sample app reuses the shared local m8flow PostgreSQL database, it
tracks its migration history in its own Alembic version table,
`m8flow_sample_app_alembic_version`, so it does not collide with the backend's
`alembic_version` row.

## Current Status

Steps 1 and 2 are in place:

- separate app package scaffold
- Postgres-default settings
- host-owned Alembic layout
- local-wheel staging flow for `m8flow-bpmn-core`
- runtime app factory
- startup `alembic upgrade head`
- static tenant and user seed data
- tenant-first session UI with standalone local selection and shared
  Keycloak browser redirect / callback sign-in

Steps 3 and 4 are also in place:

- built-in BPMN fixtures for reimbursement and timeout escalation
- built-in reimbursement workflow with conditional Finance review and
  connector-proxy SMTP email delivery
- built-in manual-review timeout workflow with supervisor escalation
- definition deployment screen
- workflow start screen
- pending task / claim / complete screens
- process instance list and detail views with metadata and events
- app-owned `secret` table aligned to the current m8flow secret schema
- list/create/edit/delete secret screens
- dedicated Alembic revision for the secret table
- host-side inline scheduler poller for timer workflows

When the sample app points at the shared local m8flow PostgreSQL database, it
reuses the existing compatible `secret` table if that table is already present.

Steps 5 through 7 are also complete:

- end-to-end smoke tests for workflow and secret CRUD behavior
- staged-wheel verification instead of source-only validation
- documented comparison with the current m8flow-style workflow path
- documented host-app integration gaps and migration notes

Shared-m8flow audit mode follow-up work now includes:

- shared-m8flow environment discovery scaffolding
- startup wiring for audit-mode context
- shared Keycloak organization and user provisioning
- shared Keycloak browser-client provisioning
- canonical tenant-id alignment to Keycloak organization ids
- backend process-model catalog publishing for m8flow UI visibility
- shared Keycloak redirect / callback login flow
- documented settings for backend-catalog sync and shared credentials

Local sample-app docs now live under [`docs/`](./docs/README.md), including a
dedicated scheduler note at [`docs/scheduler.md`](./docs/scheduler.md).

See [`../doc/sample_app.md`](../doc/sample_app.md) for the full runbook,
behavior comparison, and integration findings.

## Default Database

The sample app defaults to the local Postgres database used by the m8flow
stack:

`postgresql+psycopg://postgres:postgres@localhost:6843/postgres`

Override it with `M8FLOW_SAMPLE_APP_DATABASE_URL`.

## Shared m8flow Audit Mode

The sample app now includes shared-audit-mode support. It can detect when it
is pointed at the default local m8flow Postgres
database and now provisions the sample tenants and users into the shared
Keycloak realm during startup. Shared mode also canonicalizes
`m8flow_tenant.id` to the Keycloak organization id so the same workflow rows
can be audited through m8flow UI.

Current shared-mode behavior:

- provision sample users in the shared Keycloak realm
- reset existing shared-mode demo-user passwords so each password matches the
  username
- provision a public browser-login client in the shared Keycloak realm for the
  sample app callback URL
- align tenant ids to Keycloak organization ids
- update sample-app `user.service` / `user.service_id` to the shared realm
  issuer and real Keycloak user ids
- redirect the browser to Keycloak for shared-mode sign-in and complete the
  app session only after the callback resolves to the expected DB user
- publish or refresh BPMN files in the local m8flow backend process-model
  catalog when the process model identifier is in `<group>/<model>` format

By default the app treats database name `postgres` as the shared m8flow
database. Override the mode with:

- `M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE=auto`
- `M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE=off`
- `M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE=shared`

Useful supporting settings:

- `M8FLOW_SAMPLE_APP_M8FLOW_SHARED_DATABASE_NAME`
- `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_DIR`
- `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_CONTAINER_NAMES`
- `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_TARGET`
- `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_TENANT_ROOT`
- `M8FLOW_SAMPLE_APP_KEYCLOAK_LOGIN_CLIENT_ID`
- `M8FLOW_SAMPLE_APP_KEYCLOAK_LOGIN_PUBLIC_BASE_URLS`
- `M8FLOW_SAMPLE_APP_CONNECTOR_PROXY_BASE_URL`
- `M8FLOW_SAMPLE_APP_CONNECTOR_PROXY_TIMEOUT_SECONDS`
- `M8FLOW_SAMPLE_APP_SCHEDULER_ENABLED`
- `M8FLOW_SAMPLE_APP_SCHEDULER_POLL_SECONDS`
- `M8FLOW_SAMPLE_APP_SCHEDULER_BATCH_LIMIT`
- `M8FLOW_SAMPLE_APP_SCHEDULER_WORKER_ID`

If shared mode is active and the local Keycloak admin API cannot be reached,
startup now fails fast instead of silently seeding incompatible local-only
identities into the shared m8flow database.

## Use The Local Wheel

1. Build the library wheel from the repo root:

   Preferred:

   `uv build --wheel`

   Windows fallback if `uv build` fails with a `uv-trampoline-*.exe` temp-file
   lock error:

   `python -m pip install build hatchling`

   `python -m build --wheel --no-isolation`

2. Stage the newest wheel into the sample app vendor path:

   PowerShell:

   `.\sample_app\scripts\stage_local_wheel.ps1`

   Bash:

   `bash sample_app/scripts/stage_local_wheel.sh`

   The staging helper also refreshes the `m8flow-bpmn-core` entry in
   `sample_app/uv.lock` so the lockfile hash matches the rebuilt local wheel.
   If `sample_app/uv.lock` is missing, the helper regenerates it automatically
   before continuing.

3. Sync the sample app environment:

   `cd sample_app`

   `uv sync`

   If you are already inside the repo root `.venv`, use:

   `uv sync --active`

After the wheel is staged, the sample app resolves `m8flow-bpmn-core` from
the exact versioned wheel under `sample_app/vendor/`. The staging helper also
updates `sample_app/pyproject.toml` so `tool.uv.sources` points at that
versioned filename and refreshes the matching wheel hash recorded in
`sample_app/uv.lock`.

If you ever see a `Hash mismatch for m8flow-bpmn-core` error after rebuilding
the local wheel, rerun the staging helper or run:

`cd sample_app && uv lock --refresh-package m8flow-bpmn-core`

If you want one command that performs the whole build, stage, sync, and run
flow:

- PowerShell:

  `.\sample_app\scripts\run_sample_app.ps1`

- Bash:

  `bash sample_app/scripts/run_sample_app.sh`

Both scripts automatically switch to `uv --active` when the active virtual
environment is the repo root `.venv`. You can also force that behavior:

- PowerShell: `.\sample_app\scripts\run_sample_app.ps1 -UseActiveEnvironment`
- Bash: `bash sample_app/scripts/run_sample_app.sh --active`

On Windows, the PowerShell wrapper now retries the known transient
`uv-trampoline-*.exe` helper failures during `uv sync` and quietly falls back
to `python -m build --wheel --no-isolation` when `uv build --wheel` cannot
recover from that same class of issue. This covers the common temp-file lock
and PE-resource update failures. The raw `uv` error output is only shown when
recovery fails.

If `uv sync --active` cannot update the repo-root `.venv` because an installed
executable such as `celery.exe` is locked by another running process, the
PowerShell wrapper now falls back to syncing `sample_app/.venv` and starts the
app from there automatically.

Optional wrapper parameters:

- PowerShell: `.\sample_app\scripts\run_sample_app.ps1 -BindHost 127.0.0.1 -Port 5010`
- Bash: `bash sample_app/scripts/run_sample_app.sh 127.0.0.1 5010`

## Run The App

From `sample_app/`:

`uv run m8flow-sample-app`

If you are already inside the repo root `.venv`, use:

`uv run --active m8flow-sample-app`

By default the app runs on `127.0.0.1:5010`.

## Demo Flow

1. Open `/session/select` and choose `Sample Tenant Alpha`.
2. In standalone mode, enter directly as `alpha-admin`. In shared audit mode,
   choose `alpha-admin`, continue to Keycloak, and sign in there with
   `alpha-admin` as both username and password.
3. Go to `Process definitions` and deploy the built-in demo workflow.
4. Go to `Start workflow` and create a new process instance.
5. Open `Secrets` and update `SMTP_PASSWORD` before running the email step.
   The seeded value is intentionally `CHANGE_ME_IN_SECRETS_UI`; the sample app
   now fails fast with a clear validation error if that placeholder is still in
   use.
   Startup seeds these tenant-scoped SMTP defaults automatically:
   - `SMTP_HOST`
   - `SMTP_PORT`
   - `SMTP_USER`
   - `SMTP_PASSWORD`
   - `SMTP_STARTTLS`
   - `SMTP_FROM_EMAIL`
6. Switch identity to `alpha-operator`, claim `Submit Reimbursement Request`,
   and submit a JSON payload such as:
   - `{"requester_name":"Andre Example","requester_email":"andre@example.com","expense_description":"Conference hotel and travel","amount":1250}`
7. If the amount is greater than `1000`, switch identity to
   `alpha-finance-reviewer`, claim `Finance Review`, and submit a JSON payload.
   If Finance rejects the request, the workflow skips `Review Request` and goes
   straight to the outcome email step.
8. If Finance approved the request, switch identity to `alpha-reviewer`, claim
   `Review Request`, and submit a JSON payload with the final outcome.
9. Open `Process instances` and inspect:
   - final process status
   - persisted metadata
   - recorded event history
   - the service-task-driven HTML email outcome path

## Timeout Escalation Flow

1. Go to `Process definitions` and deploy the built-in timeout escalation
   workflow.
2. Go to `Start workflow` and create a new process instance from that
   definition.
3. Switch identity to `alpha-operator` and observe the initial
   `Review Submitted Request` manual task.
4. Leave the task open for more than two minutes. The sample app runs a
   simple inline scheduler loop and will execute the interrupting boundary
   timer automatically.
5. Switch identity to `alpha-supervisor`, claim `Supervisor Review`, and
   complete it.
6. Open `Process instances` and inspect the event history to confirm the
   original manual task was cancelled and the supervisor task completed the
   workflow.

## Tests

The sample app has its own integration smoke test suite:

`uv run pytest tests/test_end_to_end.py`

The tests use a temporary SQLite database so they can run without the local
Postgres stack, but the default interactive run path remains PostgreSQL.

## What This Proves

The sample app demonstrates that a thin host application can:

- consume `m8flow-bpmn-core` from a built wheel
- own the Alembic migration history that creates both workflow and app tables
- run workflow authorization and execution through the library
- drive one workflow end to end with task claim, conditional routing, metadata
  persistence, connector-backed email delivery, and event inspection
- drive timer-based workflow escalation from a host-managed scheduler loop
- keep host-owned tables, such as `secret`, beside the library tables without
  folding app-specific concerns into the workflow package

## Migrations

Alembic is configured under `sample_app/alembic/`.

The sample app migration environment imports:

- the library metadata from `m8flow_bpmn_core.models.Base`
- the sample app metadata from `m8flow_sample_app.models.SampleAppBase`

That lets the host app keep one migration history while still depending on the
library's ORM definitions.

The sample app uses its own Alembic version table,
`m8flow_sample_app_alembic_version`, so it can run safely against the shared
m8flow Postgres database without trying to interpret the backend's revision
ids.

Its `secret` migration also adopts an existing compatible `secret` table when
the shared database already contains one, instead of trying to recreate it.
