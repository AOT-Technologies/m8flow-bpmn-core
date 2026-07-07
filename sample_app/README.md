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
- mocked tenant and user selection UI

Steps 3 and 4 are also in place:

- built-in demo BPMN fixture
- definition deployment screen
- workflow start screen
- pending task / claim / complete screens
- process instance list and detail views with metadata and events
- app-owned `secret` table aligned to the current m8flow secret schema
- list/create/edit/delete secret screens
- dedicated Alembic revision for the secret table

When the sample app points at the shared local m8flow PostgreSQL database, it
reuses the existing compatible `secret` table if that table is already present.

Steps 5 through 7 are also complete:

- end-to-end smoke tests for workflow and secret CRUD behavior
- staged-wheel verification instead of source-only validation
- documented comparison with the current m8flow-style workflow path
- documented host-app integration gaps and migration notes

See [`../doc/sample_app.md`](../doc/sample_app.md) for the full runbook,
behavior comparison, and integration findings.

## Default Database

The sample app defaults to the local Postgres database used by the m8flow
stack:

`postgresql+psycopg://postgres:postgres@localhost:6843/postgres`

Override it with `M8FLOW_SAMPLE_APP_DATABASE_URL`.

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

3. Sync the sample app environment:

   `cd sample_app`

   `uv sync`

   If you are already inside the repo root `.venv`, use:

   `uv sync --active`

After the wheel is staged, the sample app resolves `m8flow-bpmn-core` from
the exact versioned wheel under `sample_app/vendor/`. The staging helper also
updates `sample_app/pyproject.toml` so `tool.uv.sources` points at that
versioned filename.

## Run The App

From `sample_app/`:

`uv run m8flow-sample-app`

If you are already inside the repo root `.venv`, use:

`uv run --active m8flow-sample-app`

By default the app runs on `127.0.0.1:5010`.

## Demo Flow

1. Open `/session/select` and choose `Sample Tenant Alpha`.
2. Sign in as `alpha-admin`.
3. Go to `Process definitions` and deploy the built-in demo workflow.
4. Go to `Start workflow` and create a new process instance.
5. Switch identity to `alpha-operator`, claim `Prepare Request`, and submit a
   JSON payload.
6. Switch identity to `alpha-reviewer`, claim `Review Request`, and submit a
   JSON payload.
7. Open `Process instances` and inspect:
   - final process status
   - persisted metadata
   - recorded event history
8. Open `Secrets` to create or update tenant-scoped secrets using the app-owned
   table.

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
- drive one workflow end to end with task claim, task completion, metadata
  persistence, and event inspection
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
