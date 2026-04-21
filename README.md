# m8flow-bpmn-core

Initial scaffold for the M8Flow BPMN core library.

## Included

- `pyproject.toml` with a modern `uv`-friendly layout
- SQLAlchemy ORM bootstrap
- Alembic migration scaffolding
- Environment-based database settings
- A direct Python API surface in `m8flow_bpmn_core.api`
- A lightweight command/query layer in `m8flow_bpmn_core.application`
- Process lifecycle commands for suspend, resume, and terminate
- A small smoke-test baseline

## Architecture

This package is a library, not an HTTP service.
It is meant to be called directly by the platform or app layer through Python imports.

## Local Setup

1. Create or refresh the local virtual environment with `python -m venv .venv`.
2. Install `uv` if it is not already available.
3. Sync dependencies with `uv sync`.
4. Run tests with `uv run pytest`.
5. Apply migrations with `uv run alembic upgrade head`.

## Development Commands

Use `uv run` so the commands always execute inside the synced project environment.

### Ruff

- Check the codebase:

  `uv run ruff check src tests alembic/versions`

- Auto-fix safe issues:

  `uv run ruff check --fix src tests alembic/versions`

- If you add or rename imports, Ruff will also keep the import order consistent through `ruff check`.

### Tests

- Run the full test suite:

  `uv run pytest`

- Run a focused test file:

  `uv run pytest tests/test_application_layer.py`

- Run a single test:

  `uv run pytest tests/test_application_layer.py -k retry`

The test suite is designed to run against the library directly, without starting an HTTP server.

### Postgres Integration

- Run the local Postgres smoke test:

  `uv run pytest integration/test_postgres_integration.py`

This test starts a temporary `postgres:16` container with Docker, applies the
schema, exercises the public API against a live PostgreSQL database, and rolls
the transaction back at the end.
It requires Docker and the Postgres driver from `uv sync --extra postgresql`.
The same file also includes a conditional approval workflow smoke test that
imports the BPMN and DMN fixtures, starts the process by definition id, and
drives the submit, manager, and finance tasks through the API.

### POC Workflow

- Run the end-to-end invoice approval demo:

  `uv run pytest tests/test_workflow_poc.py`

This test seeds an example workflow and drives it through the public library API:
claim, suspend, resume, error, retry, complete, metadata updates, event logging, and termination.
The BPMN fixture used for the demo lives in [tests/fixtures/invoice_approval_poc.bpmn](C:/dev/repos/m8flow-bpmn-core/tests/fixtures/invoice_approval_poc.bpmn).
The fixture includes an exclusive gateway with approved and rejected branches, and
the scenario test changes variables such as `approval_state`, `approval_amount`,
`decision_note`, and `decision_path` to demonstrate both paths.

- Run the conditional approval demo with lanes and user assignment:

  `uv run pytest tests/test_conditional_approval_poc.py`

The BPMN fixture used for this demo lives in [tests/fixtures/conditional-approval.bpmn](tests/fixtures/conditional-approval.bpmn).
The demo now uses the public `ImportBpmnProcessDefinitionCommand` to create the
process definition from the BPMN and DMN source, then uses
`InitializeProcessInstanceFromDefinitionCommand` to create the process instance,
store the submission payload, initialize the SpiffWorkflow-backed runtime, and
materialize the first pending task.
The fixture includes lane-based assignment for Manager and Finance, a script task
that declares the `lane_owners` mapping, and conditional branches for manager
approval, auto-approval, and finance escalation driven by the `amount` DMN.

## Python

This project targets Python 3.12.

## Database

The default configuration uses SQLite for local development.
Set `M8FLOW_DATABASE_URL` to point at PostgreSQL or MySQL when you are ready
to switch to a server-backed database.
Optional database drivers are exposed as `postgresql` and `mysql` extras in
`pyproject.toml`.

To run the test suite against PostgreSQL, install the `postgresql` extra and
set `M8FLOW_TEST_DATABASE_URL` to a dedicated test database URL, for example:

`postgresql+psycopg://user:password@localhost:5432/m8flow_bpmn_core_test`

If you already manage a SQLAlchemy `Connection` and transaction, pass that
connection directly to `execute_command(...)` or `execute_query(...)`.
The library will use your transaction boundary and will not commit or roll back
for you.
