# m8flow-core sample app

A small Flask web application that uses m8flow-core as a wheel
dependency. It deploys BPMN workflows, starts them, and lets you claim and
complete tasks in the browser, with timers, an SMTP service task, and
per-tenant secrets.

Use it to see the library working inside a real host application, or as a
starting point for your own. [About the sample app integration](../docs/explanation/sample-app-integration.md)
explains the design and what it found.

## Before you begin

You need the following:

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- PostgreSQL. By default, the app uses the database of a local m8flow stack
  at `localhost:6843`. To use another database, set
  `M8FLOW_SAMPLE_APP_DATABASE_URL`.
- For the reimbursement workflow's email step, a running
  `m8flow-connector-proxy`, by default at `http://localhost:6844`, and an SMTP
  account.

## Run the app

The run script builds the library wheel, copies it into `sample_app/vendor/`,
syncs the app's environment, and starts the app. From the repository root,
run the following command:

```bash
bash sample_app/scripts/run_sample_app.sh
```

On Windows, run `.\sample_app\scripts\run_sample_app.ps1`.

The app starts on `http://127.0.0.1:5010`. To use a different host and port,
pass them to the script, such as `bash sample_app/scripts/run_sample_app.sh
127.0.0.1 5020`, or `-BindHost` and `-Port` in PowerShell.

On startup, the app applies its migrations, creates the sample tenants, users,
permissions, and default secrets, and starts its scheduler loop.

### Run the steps yourself

To build, stage, and run the app step by step, follow these steps:

1. Build the library wheel from the repository root:

   ```bash
   uv build --wheel
   ```

   If this fails on Windows with a locked `uv-trampoline-*.exe` file, run
   `python -m pip install build hatchling` and then
   `python -m build --wheel --no-isolation`.

2. Copy the newest wheel into `sample_app/vendor/`:

   ```bash
   bash sample_app/scripts/stage_local_wheel.sh
   ```

   On Windows, run `.\sample_app\scripts\stage_local_wheel.ps1`. The script
   also points `sample_app/pyproject.toml` at the wheel and refreshes its hash
   in `sample_app/uv.lock`.

3. Sync the app's environment and start it:

   ```bash
   cd sample_app
   uv sync
   uv run m8flow-sample-app
   ```

   If the repository's `.venv` is active, use `uv sync --active` and
   `uv run --active m8flow-sample-app` instead, so uv doesn't create
   `sample_app/.venv`. The run scripts do this for you; to force it, pass
   `--active` or `-UseActiveEnvironment`.

If you see `Hash mismatch for m8flow-core` after rebuilding the wheel,
run the staging script again, or run
`cd sample_app && uv lock --refresh-package m8flow-core`.

## Try the reimbursement workflow

The reimbursement workflow routes requests over 1000 through Finance and
emails the outcome through an SMTP service task.

To run it, follow these steps:

1. Open `http://127.0.0.1:5010/session/select` and choose
   **Sample Tenant Alpha** and `alpha-admin`. In shared mode, the app sends
   you to Keycloak; sign in with `alpha-admin` as both username and password.
2. In **Process definitions**, deploy the built-in demo workflow.
3. In **Secrets**, replace the `SMTP_PASSWORD` value and check the other SMTP
   settings: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_STARTTLS`, and
   `SMTP_FROM_EMAIL`. The seeded password is `CHANGE_ME_IN_SECRETS_UI`, and the
   email step stops with an error until you change it.
4. In **Start workflow**, start a process instance.
5. Switch to `alpha-operator`, claim **Submit Reimbursement Request**, and
   submit a JSON payload such as the following:

   ```json
   {"requester_name": "Andre Example", "requester_email": "andre@example.com", "expense_description": "Conference hotel and travel", "amount": 1250}
   ```

6. Because the amount is over 1000, switch to `alpha-finance-reviewer`, claim
   **Finance Review**, and submit a decision. If Finance rejects, the workflow
   skips the final review and sends the outcome email.
7. If Finance approved, switch to `alpha-reviewer`, claim **Review Request**,
   and submit the final outcome.
8. In **Process instances**, open the instance to check its status, metadata,
   and event history.

You can also deploy your own workflow: upload a `.bpmn` file in
**Custom workflow from BPMN file**, or, in shared mode, enter a
`<group>/<model>` identifier in **Import existing model from local m8flow
catalog**.

## Try the timeout escalation workflow

The timeout escalation workflow escalates an unanswered review to a
supervisor after two minutes.

To run it, follow these steps:

1. In **Process definitions**, deploy the built-in timeout escalation
   workflow.
2. In **Start workflow**, start a process instance from it.
3. Switch to `alpha-operator` and find the **Review Submitted Request** task.
   Leave it open for more than two minutes.
4. Switch to `alpha-supervisor`, then claim and complete **Supervisor Review**.
5. In **Process instances**, check that the event history shows the original
   task as cancelled and the supervisor task as completed.

## Shared audit mode

In shared audit mode, the app uses the database of a local m8flow stack, so
you can watch its workflows in the m8flow UI. It creates the sample tenants
and users in the shared Keycloak realm, signs users in through Keycloak, and
publishes deployed models to the m8flow backend's process catalog.

The mode is set by `M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE`:

| Value | Behavior |
|---|---|
| `auto` | Default. Uses shared mode when the database name matches `M8FLOW_SAMPLE_APP_M8FLOW_SHARED_DATABASE_NAME`, which is `postgres` by default. |
| `shared` | Always uses shared mode |
| `off` | Always uses standalone mode, with a local tenant and user picker |

If shared mode is on and Keycloak can't be reached, the app doesn't start.

## Configuration

The app reads these environment variables:

| Variable | Default | Description |
|---|---|---|
| `M8FLOW_SAMPLE_APP_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:6843/postgres` | Database URL |
| `M8FLOW_SAMPLE_APP_DATABASE_ECHO` | `false` | Logs every SQL statement when `true` |
| `M8FLOW_SAMPLE_APP_SECRET_KEY` | `sample-app-dev-secret` | Flask session key. Change it anywhere other than your own machine. |
| `M8FLOW_SAMPLE_APP_HOST` | `127.0.0.1` | Bind address |
| `M8FLOW_SAMPLE_APP_PORT` | `5010` | Port |
| `M8FLOW_SAMPLE_APP_DEBUG` | `false` | Flask debug mode |
| `M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE` | `auto` | `auto`, `shared`, or `off`. See [Shared audit mode](#shared-audit-mode). |
| `M8FLOW_SAMPLE_APP_M8FLOW_SHARED_DATABASE_NAME` | `postgres` | Database name that `auto` treats as the shared m8flow database |
| `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_DIR` | Discovered | Local path of the m8flow backend's process model catalog |
| `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_CONTAINER_NAMES` | `m8flow-m8flow-backend-1,m8flow-backend,m8flow-backend-1` | Docker containers to search for the catalog mount |
| `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_PROCESS_MODELS_TARGET` | `/app/data/process_models` | Catalog mount path inside the backend container |
| `M8FLOW_SAMPLE_APP_M8FLOW_BACKEND_TENANT_ROOT` | Discovered | Tenant folder used when publishing models to the catalog |
| `M8FLOW_SAMPLE_APP_KEYCLOAK_LOGIN_CLIENT_ID` | `m8flow-sample-app` | Keycloak client for the app's sign-in |
| `M8FLOW_SAMPLE_APP_KEYCLOAK_LOGIN_PUBLIC_BASE_URLS` | Derived from host and port | Comma-separated public base URLs allowed for the Keycloak callback |
| `M8FLOW_SAMPLE_APP_CONNECTOR_PROXY_BASE_URL` | `http://localhost:6844` | Base URL of `m8flow-connector-proxy` |
| `M8FLOW_SAMPLE_APP_CONNECTOR_PROXY_TIMEOUT_SECONDS` | `10.0` | Connector proxy request timeout |
| `M8FLOW_SAMPLE_APP_SCHEDULER_ENABLED` | `true` | Runs the scheduler loop |
| `M8FLOW_SAMPLE_APP_SCHEDULER_POLL_SECONDS` | `1.0` | Seconds between scheduler passes |
| `M8FLOW_SAMPLE_APP_SCHEDULER_BATCH_LIMIT` | `100` | Most jobs per scheduler pass |
| `M8FLOW_SAMPLE_APP_SCHEDULER_WORKER_ID` | `sample-app-inline-scheduler` | Worker ID stored on claimed jobs |

## Run the tests

The app has its own end-to-end tests. They use a temporary SQLite database, so
they don't need PostgreSQL. From `sample_app/`, run the following command:

```bash
uv run pytest tests/test_end_to_end.py
```

## Migrations

The app owns its Alembic migrations in `sample_app/alembic/`, including the
ones that create the library's tables, because the library wheel doesn't ship
migrations. It records its history in its own version table,
`m8flow_sample_app_alembic_version`, so it can share a database with the
m8flow backend.

## Related pages

- [Sample app architecture](docs/README.md)
- [Sample app scheduler](docs/scheduler.md)
- [About the sample app integration](../docs/explanation/sample-app-integration.md)
