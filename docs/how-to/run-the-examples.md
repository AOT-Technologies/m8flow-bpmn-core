# Run the examples

The `examples/` folder holds runnable scripts that drive real workflows
through the public API. This guide lists them, shows how to run each kind, and
explains how the PostgreSQL examples share a database with a local m8flow
stack.

## Available examples

The repository has the following examples:

| Script | What it shows | Database |
|---|---|---|
| `parallel_review_poc.py` | A purchase order through a parallel gateway, script tasks, and an exclusive gateway, across three lanes | In-memory SQLite |
| `errors_demo.py` | Every public error class, raised for real | In-memory SQLite |
| `conditional_approval_poc.py` | An approval with an exclusive gateway, a DMN decision, lane assignment, and command permission checks | PostgreSQL |
| `conditional_approval_rejection_poc.py` | The same approval, where the manager rejects and the Finance lane never starts | PostgreSQL |
| `scheduled_timer_poc.py` | A timer start event fired by an inline scheduler loop | PostgreSQL |
| `celery_timer_poc.py` | The same timer start event, fired by a separate Celery worker | PostgreSQL and Redis |
| `scheduled_boundary_timer_poc.py` | An interrupting boundary timer that cancels a waiting task and starts the timeout path | PostgreSQL |
| `scheduled_cycle_timer_poc.py` | A recurring `timeCycle` timer start (`R3/PT20S`) that creates three instances | PostgreSQL |
| `scheduled_retry_poc.py` | A delayed retry that reopens an errored instance and its task | PostgreSQL |
| `service_task_connector_poc.py` | Two BPMN service tasks run through a live `m8flow-connector-proxy` | PostgreSQL and the connector proxy |
| `service_task_failure_retry_poc.py` | A service task that fails on start, survives the caller's rollback, and succeeds on retry | PostgreSQL |

The BPMN and DMN files they use are in `tests/fixtures/`.

## Run the SQLite examples

The SQLite examples need nothing beyond `uv sync`. They run without pausing
and print each step. To run them, use the following commands:

```bash
uv run python examples/parallel_review_poc.py
uv run python examples/errors_demo.py
```

In `parallel_review_poc.py`, change `SCENARIO` near the top of the file to
`finance_rejects` or `compliance_rejects` to follow the rejection branches.
The interesting moment is right after Finance completes its review: the
Compliance branch is still pending, so the workflow waits at the join.

`errors_demo.py` checks that each error is both its domain class and the
matching built-in exception, and exits with a non-zero status if any case
fails. Its output is similar to the following:

```text
--------------------------------------------------------------------------------
Case: NotFoundError
Reading a process instance that does not exist.
  raised:   NotFoundError: Process instance 999999 was not found for tenant tenant-errors-demo
  domain:   NotFoundError (also subclass of LookupError)
```

## Run the PostgreSQL examples

The PostgreSQL examples are interactive. They pause before and after each
command and explain what it does.

To run one, follow these steps:

1. Install the PostgreSQL driver:

   ```bash
   uv sync --extra postgresql
   ```

2. Run the example's launcher. For the conditional approval example, use the
   Bash launcher:

   ```bash
   bash examples/conditional_approval_poc.sh
   ```

   Or use the PowerShell launcher:

   ```powershell
   .\examples\conditional_approval_poc.ps1
   ```

Every PostgreSQL example has a `.sh` and a `.ps1` launcher with the same name.
The launcher uses the database described in
[Choose the database](#choose-the-database).

To run an example without a launcher, set `M8FLOW_EXAMPLE_DATABASE_URL` and
run the script directly, for example
`uv run python examples/scheduled_timer_poc.py`.

### Choose the database

A launcher first looks for the database of a local m8flow stack at
`localhost:6843/postgres`:

- **If it's reachable**, the example asks for confirmation, then runs in
  shared mode, described in the following section.
- **If it isn't reachable**, or you decline, the example starts a temporary
  PostgreSQL container in Docker instead.

Launcher options:

| Bash | PowerShell | Effect |
|---|---|---|
| `--docker` | `-UseDocker` | Always use a temporary container |
| `--keep-container` | `-KeepContainer` | Leave the temporary container running after the example exits |

### Watch a workflow in the m8flow UI

In shared mode, the example works against the same database as your local
m8flow stack, so you can watch the process instance in the m8flow UI while the
script runs. To make that work, the example does the following:

- It publishes the BPMN and DMN files into the m8flow backend's process model
  catalog, under the `m8flow-core-examples` process group.
- It creates the example tenants as organizations, and the demo users, in the
  local Keycloak realm at `http://localhost:6842/realms/m8flow`. It then
  aligns the tenant IDs and user records with Keycloak, so the same accounts
  work in the example and in the UI.
- It keeps the demo data after the run. If the tenant, users, or process model
  already exist, it warns and reuses them.

New Keycloak demo users get the password `poc-demo-password`. To use a
different one, set `M8FLOW_EXAMPLE_KEYCLOAK_PASSWORD`.

## Run the Celery timer example

The Celery timer example runs the scheduler in a separate process, so it needs
two terminals and a Redis broker. By default, it uses the Redis of a local
m8flow stack at `redis://localhost:6848/0`.

To run it, follow these steps:

1. Install the PostgreSQL and Celery extras:

   ```bash
   uv sync --extra postgresql --extra celery
   ```

2. In the first terminal, start the Celery beat and worker:

   ```bash
   bash examples/celery_scheduler_worker.sh
   ```

   On Windows, run `.\examples\celery_scheduler_worker.ps1`.

3. In the second terminal, start the workflow:

   ```bash
   bash examples/celery_timer_poc.sh --use-existing-worker
   ```

   On Windows, run `.\examples\celery_timer_poc.ps1 -UseExistingWorker`.

The worker listens on its own queue, `m8flow-core-poc`. To use a
different broker, result backend, or queue, set the
`M8FLOW_CORE_CELERY_*` variables listed in
[Configuration](../reference/configuration.md#examples). On Windows, the
helper starts `celery beat` as a separate hidden process and runs the worker
with `--pool solo`, because Celery doesn't support `worker --beat` there.

To start Celery yourself instead of with the helper, run beat and the worker
in the first terminal:

```bash
celery -A examples.celery_scheduler_poc:celery_app beat --loglevel info
celery -A examples.celery_scheduler_poc:celery_app worker --pool solo \
    --loglevel info -Q m8flow-core-poc
```

Then run `uv run python examples/celery_timer_poc.py` in the second terminal.

## Service task examples

`service_task_connector_poc.py` needs a running `m8flow-connector-proxy`, by
default at `http://localhost:6844`. It starts a small HTTP server on your
machine and points the BPMN service tasks at it through the proxy's
`http/GetRequestV2` command. If the proxy runs in Docker, it reaches your
machine through `host.docker.internal`. To change the proxy URL or the host
alias, set `M8FLOW_CONNECTOR_PROXY_BASE_URL` or
`M8FLOW_CONNECTOR_POC_HOST_ALIAS`.

`service_task_failure_retry_poc.py` needs no proxy. It starts the instance
with an empty connector registry, so the first service task fails, lets the
error escape a rolling-back transaction, and shows the instance still saved in
`error`. It then installs a working in-process connector and retries the same
instance to completion.

## Related pages

- [Sample app](../../sample_app/README.md): a web application that uses the
  library as a dependency
- [Run timers and scheduled retries](run-timers-and-retries.md)
- [Call external services from service tasks](call-external-services.md)
- [Configuration](../reference/configuration.md#examples)
