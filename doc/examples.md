# Example Workflows

The repository ships ten runnable examples:

| File | Purpose | Database |
| --- | --- | --- |
| `examples/conditional_approval_poc.py` | Interactive full approval flow (XOR gateway, DMN). | PostgreSQL |
| `examples/conditional_approval_rejection_poc.py` | Manager-rejection variant of the flow above. | PostgreSQL |
| `examples/scheduled_timer_poc.py` | Interactive timer-start workflow with inline polling and m8flow audit support. | PostgreSQL |
| `examples/celery_timer_poc.py` | Interactive timer-start workflow that waits for a separate Celery poller to trigger the persisted scheduler row. | PostgreSQL |
| `examples/scheduled_boundary_timer_poc.py` | Interactive boundary-timer workflow that interrupts an active user task through inline polling. | PostgreSQL |
| `examples/scheduled_cycle_timer_poc.py` | Interactive recurring timer-start workflow with `timeCycle` rescheduling and m8flow audit support. | PostgreSQL |
| `examples/scheduled_retry_poc.py` | Interactive delayed-retry workflow with inline polling and m8flow audit support. | PostgreSQL |
| `examples/service_task_connector_poc.py` | Interactive connector-proxy workflow with real BPMN service tasks and m8flow audit support. | PostgreSQL |
| `examples/parallel_review_poc.py` | Non-interactive purchase-order flow that exercises a parallel gateway and script tasks. | In-memory SQLite |
| `examples/errors_demo.py` | Non-interactive walk through every public error class. | In-memory SQLite |

The two conditional-approval examples are interactive walkthroughs that
exercise an exclusive gateway with a DMN-driven branch. The parallel-review
example covers the BPMN shapes the conditional-approval flow does not —
parallel gateway (AND-split + AND-join) and Python script tasks. The errors
demo is a quick, dependency-free way to see each `BpmnCoreError` subclass
being raised by the services.

## Sample App

In addition to the workflow POCs above, the repo now includes a thin host-app
integration under `sample_app/`. It validates the “consume the library as a
dependency” path rather than the standalone example-script path.

See [sample_app.md](sample_app.md) for:

- wheel staging and dependency setup
- the default PostgreSQL run path
- the tenant and user selection flow
- end-to-end workflow execution through the web app
- behavior comparison with the current m8flow-style workflow path
- documented integration findings and current API gaps

## How To Run

Most interactive examples use one terminal:

- install PostgreSQL support:

  `uv sync --extra postgresql`

- run the desired Python example directly, or use its matching shell launcher
  from `examples/`

The Celery timer POC is the exception. It is intended to demonstrate a host
application in one process and a separate Celery-driven scheduler poller in
another, so run it in **two terminals**.

Install the required extras first:

`uv sync --extra postgresql --extra celery`

Then start:

1. Terminal 1, the Celery poller helper:

   `.\examples\celery_scheduler_worker.ps1`

   `bash examples/celery_scheduler_worker.sh`

2. Terminal 2, the workflow POC:

   `.\examples\celery_timer_poc.ps1 -UseExistingWorker`

   `bash examples/celery_timer_poc.sh --use-existing-worker`

If you prefer raw Celery commands instead of the PowerShell helper, start
`celery beat` and `celery worker` in the poller terminal and then run
`uv run python examples/celery_timer_poc.py` in the workflow terminal.

## Conditional Approval

- Full approval path:

  `uv run python examples/conditional_approval_poc.py`

- Manager rejection path:

  `uv run python examples/conditional_approval_rejection_poc.py`

Both examples:

- create or connect to a Postgres database
- print the DB connection details for tools like DBeaver
- import the BPMN and DMN fixtures
- start a process instance from the stored definition id
- list pending tasks for the requester, manager, reviewer, and finance users
- claim and complete tasks through the public API
- attach the form payload to task completion through `CompleteTaskCommand`
- exercise the built-in V1 RBAC checks for `process_definition.import`,
  `process.start`, `task.claim`, and `task.complete`

The rejection variant changes the manager decision to `Rejected`, so the flow
ends before the Finance lane is activated.

## Scheduled Timer

`examples/scheduled_timer_poc.py` is an interactive walkthrough of the new
scheduler path. It demonstrates a timer-start BPMN model that is imported into
the library, persisted as a `scheduler_job`, and then triggered by a simple
inline poller loop owned by the host application.

Run it with:

```bash
uv run python examples/scheduled_timer_poc.py
```

Or on PowerShell:

```powershell
.\examples\scheduled_timer_poc.ps1
```

Or on Bash:

```bash
bash examples/scheduled_timer_poc.sh
```

The example:

- creates or connects to a Postgres database
- reuses the shared local m8flow database when available, or starts a
  temporary Postgres container otherwise
- provisions a demo tenant and users
- deploys the rendered BPMN into the local m8flow backend catalog when the
  shared DB is in use, so the model can be audited in the UI
- imports a timer-start definition scheduled a few seconds in the future
- shows the persisted scheduler row
- runs an inline `api.run_due_scheduler_jobs(...)` loop until the timer fires
- lists the timer-created process instance and the operator task
- claims and completes the task through the public API

This is the shortest end-to-end example of how an application can use
`m8flow_bpmn_core` to manage a timer-driven workflow without adding Celery or
another external scheduler dependency.

The same script also supports an external scheduler mode through
`M8FLOW_SCHEDULER_EXECUTION_MODE=external`. That is what the dedicated Celery
POC wrapper uses.

## Celery Timer

`examples/celery_timer_poc.py` is the Celery-backed counterpart to
`scheduled_timer_poc.py`. It reuses the same BPMN model, shared-DB deployment,
and audit flow, but it does not call the inline poller. Instead it waits for a
Celery-driven poller to consume the persisted scheduler row through the public
`api.run_due_scheduler_jobs(...)` entrypoint.

Install the required extras first:

```bash
uv sync --extra postgresql --extra celery
```

This POC is intended to run in two terminals so the scheduler worker is
clearly separate from the application process.

Start the helper in terminal 1:

```powershell
.\examples\celery_scheduler_worker.ps1
```

Or on Bash:

```bash
bash examples/celery_scheduler_worker.sh
```

Then run the workflow POC in terminal 2:

```powershell
.\examples\celery_timer_poc.ps1 -UseExistingWorker
```

Or on Bash:

```bash
bash examples/celery_timer_poc.sh --use-existing-worker
```

If you prefer raw Celery commands instead of the PowerShell helper, start beat
and worker in terminal 1 instead:

```bash
celery -A examples.celery_scheduler_poc:celery_app beat --loglevel info
celery -A examples.celery_scheduler_poc:celery_app worker --pool solo --loglevel info -Q m8flow-bpmn-core-poc
```

Then run the timer POC in terminal 2:

```bash
uv run python examples/celery_timer_poc.py
```

The Celery scheduler worker helper:

- defaults to the local m8flow Redis host port `redis://localhost:6848/0`
- can inherit `M8FLOW_BACKEND_CELERY_BROKER_URL` and
  `M8FLOW_BACKEND_CELERY_RESULT_BACKEND` if those are already exported
- uses its own queue name, `m8flow-bpmn-core-poc`, by default
- starts a hidden `celery beat` helper on Windows and keeps the actual Celery
  worker in the foreground
- uses `--pool solo` for the worker so the POC behaves predictably on Windows

The timer POC itself:

- imports the same timer-start BPMN definition as the inline example
- persists the timer-start `scheduler_job` row
- waits for the Celery-driven scheduler worker to create the process instance
- then claims and completes the resulting user task through the public API

This is the smallest example of how an application can reuse m8flow's Redis
infrastructure while still keeping the BPMN-aware execution inside
`m8flow_bpmn_core`.

## Scheduled Boundary Timer

`examples/scheduled_boundary_timer_poc.py` is the interrupting-boundary-event
counterpart to the timer-start example above. It demonstrates a normal process
instance with an attached timer boundary event that is persisted in
`scheduler_job`, fired by the inline poller, and then switched onto the
timeout path on the same process instance.

Run it with:

```bash
uv run python examples/scheduled_boundary_timer_poc.py
```

Or on PowerShell:

```powershell
.\examples\scheduled_boundary_timer_poc.ps1
```

Or on Bash:

```bash
bash examples/scheduled_boundary_timer_poc.sh
```

The example:

- creates or connects to a Postgres database
- reuses the shared local m8flow database when available, or starts a
  temporary Postgres container otherwise
- provisions a dedicated demo tenant and users for the boundary-timer example
- deploys the BPMN into the local m8flow backend catalog when the shared DB
  is in use, so the model can be audited in the UI
- imports a workflow whose first user task has an interrupting timer boundary
  event due one minute in the future
- starts a normal process instance through the public API
- shows the original review task and the persisted instance-scoped
  `intermediate_timer` scheduler row
- runs an inline `api.run_due_scheduler_jobs(...)` loop until the timeout path
  becomes active
- demonstrates that the original review task is closed as `CANCELLED`
- demonstrates that the timeout-path task becomes READY on the same process
  instance
- claims and completes that timeout task through the public API

This is the shortest end-to-end example of how an application can use
`m8flow_bpmn_core` to manage an interrupting timer boundary event without
adding Celery or another external scheduler dependency.

## Scheduled Cycle Timer

`examples/scheduled_cycle_timer_poc.py` is the recurring counterpart to the
single-shot timer example above. It demonstrates a timer-start BPMN model that
uses a quoted BPMN `timeCycle` expression, persists the definition-level
`scheduler_job`, waits through all configured cycle occurrences, and then shows
the cycle-created process instances after the finite recurring schedule is
exhausted.

Run it with:

```bash
uv run python examples/scheduled_cycle_timer_poc.py
```

Or on PowerShell:

```powershell
.\examples\scheduled_cycle_timer_poc.ps1
```

Or on Bash:

```bash
bash examples/scheduled_cycle_timer_poc.sh
```

The example:

- creates or connects to a Postgres database
- reuses the shared local m8flow database when available, or starts a
  temporary Postgres container otherwise
- provisions a dedicated demo tenant and users for the cycle example
- deploys the rendered BPMN into the local m8flow backend catalog when the
  shared DB is in use, so the model can be audited in the UI
- imports a recurring timer-start definition with a quoted `timeCycle`
  expression (`R3/PT20S` in the current POC)
- shows the persisted scheduler row, including the first computed due time
- runs an inline `api.run_due_scheduler_jobs(...)` loop until all three cycle
  occurrences fire
- demonstrates that three separate process instances are created, one per cycle
- confirms that the recurring scheduler row is deleted after the finite cycle
  is exhausted
- shows the three generated operator tasks and leaves them in place for audit
  or later completion through the public API

This is the shortest end-to-end example of how an application can use
`m8flow_bpmn_core` to manage a recurring timer-driven workflow without adding
Celery or another external scheduler dependency.

## Scheduled Retry

`examples/scheduled_retry_poc.py` is the delayed-retry counterpart to the
timer examples above. It demonstrates a normal user-task workflow that is
started through the public API, moved into `error`, scheduled for retry as a
persisted `process_retry` scheduler row, and then reopened by the same inline
poller interface used for timer jobs.

Run it with:

```bash
uv run python examples/scheduled_retry_poc.py
```

Or on PowerShell:

```powershell
.\examples\scheduled_retry_poc.ps1
```

Or on Bash:

```bash
bash examples/scheduled_retry_poc.sh
```

The example:

- creates or connects to a Postgres database
- reuses the shared local m8flow database when available, or starts a
  temporary Postgres container otherwise
- provisions a dedicated demo tenant and users for the retry example
- deploys the BPMN into the local m8flow backend catalog when the shared DB
  is in use, so the model can be audited in the UI
- imports and starts a simple user-task workflow through the public API
- marks that same process instance `error`
- persists a delayed `process_retry` scheduler row for that instance
- runs an inline `api.run_due_scheduler_jobs(...)` loop until the retry fires
- demonstrates that the same process instance returns to `running`
- demonstrates that the same human task id is reopened to `READY`
- claims and completes that reopened task through the public API
- leaves the completed retried instance and event history in place for audit

This is the shortest end-to-end example of how an application can use
`m8flow_bpmn_core` to manage delayed retry without adding Celery or another
external scheduler dependency.

## Service Task Connector

`examples/service_task_connector_poc.py` is the connector-proxy counterpart to
the timer and retry examples above. It demonstrates a BPMN model with two real
`ServiceTask` nodes, builds a live registry from the local
`m8flow-connector-proxy` catalog, and then executes those service tasks through
the public runtime while keeping the workflow auditable in m8flow.

Run it with:

```bash
uv run python examples/service_task_connector_poc.py
```

Or on PowerShell:

```powershell
.\examples\service_task_connector_poc.ps1
```

Or on Bash:

```bash
bash examples/service_task_connector_poc.sh
```

The example:

- creates or connects to a Postgres database
- reuses the shared local m8flow database when available, or starts a
  temporary Postgres container otherwise
- provisions a dedicated demo tenant and users for the service-task example
- deploys the BPMN into the local m8flow backend catalog when the shared DB
  is in use, so the model can be audited in the UI
- queries the live `m8flow-connector-proxy` `/v1/commands` catalog and builds
  a `ServiceTaskRegistry` from it
- starts a host-side demo HTTP endpoint and points the BPMN service tasks at
  that endpoint through the proxy using `http/GetRequestV2`
- starts the process instance through the public API, which executes the first
  service task immediately
- stops at a user task, then claims and completes it through the public API
- executes the second service task after the user task is completed
- prints the captured external requests and the persisted workflow data so the
  connector results can be inspected after the run

This is the shortest end-to-end example of how an application can use
`m8flow_bpmn_core` to execute real BPMN service tasks through m8flow's current
connector-proxy direction while still keeping the workflow logic in-process.

## Launchers

The repository also includes shell launchers that first try the shared local
Postgres database used by a nearby m8flow instance on `localhost:6843/postgres`.
When that shared database is reachable, the interactive example asks for
confirmation before proceeding, keeps the demo data in place after the run,
reuses existing seed rows with warnings instead of failing. In that shared-DB
mode, the conditional-approval example also tries to publish its BPMN and DMN
files into the local m8flow backend process-model catalog so the model appears
in the m8flow UI. It also provisions the example tenants and demo users in the
local Keycloak shared realm (`http://localhost:6842/realms/m8flow` by default)
through `m8flow_bpmn_core.utils.keycloak`, then mirrors the local `user`
records to the shared-realm issuer and Keycloak user ids so the same accounts
work in both the example and the UI. In that mode the example also aligns the
local `m8flow_tenant.id` values to the Keycloak organization UUIDs, because the
m8flow backend resolves the active tenant from those organization ids during
shared-realm login finalization. New Keycloak demo users default to
password `poc-demo-password` unless `M8FLOW_EXAMPLE_KEYCLOAK_PASSWORD` is set.
If the tenant, users, or deployed process model already exist, the example
warns and leaves the existing Keycloak/backend data in place. If the shared
database is not reachable, the launchers start a temporary Docker container
instead. If the prompt appears and you decline the shared database, the Python
example also starts the same temporary Docker fallback. When the shared m8flow
backend is in use, the example stores the
process model identifier as
`m8flow-bpmn-core-examples/conditional-approval-poc` so process instance links
resolve to the deployed model in the UI. In that shared-DB mode you can watch
the process instance progress and audit it live in the m8flow UI while
`conditional_approval_poc.py` is still running.

- PowerShell:

  `.\examples\conditional_approval_poc.ps1`

  `.\examples\conditional_approval_rejection_poc.ps1`

  `.\examples\scheduled_timer_poc.ps1`

  `.\examples\celery_scheduler_worker.ps1`

  `.\examples\celery_timer_poc.ps1 -UseExistingWorker`

  `.\examples\scheduled_boundary_timer_poc.ps1`

  `.\examples\scheduled_cycle_timer_poc.ps1`

  `.\examples\scheduled_retry_poc.ps1`

  `.\examples\service_task_connector_poc.ps1`

- Bash:

  `bash examples/conditional_approval_poc.sh`

  `bash examples/conditional_approval_rejection_poc.sh`

  `bash examples/scheduled_timer_poc.sh`

  `bash examples/celery_scheduler_worker.sh`

  `bash examples/celery_timer_poc.sh --use-existing-worker`

  `bash examples/scheduled_boundary_timer_poc.sh`

  `bash examples/scheduled_cycle_timer_poc.sh`

  `bash examples/scheduled_retry_poc.sh`

  `bash examples/service_task_connector_poc.sh`

Add `-UseDocker` or `--docker` to force the temporary container. Add
`-KeepContainer` or `--keep-container` if you want to leave the container
running after the example exits.

## What The Example Demonstrates

- A workflow can be imported from BPMN and DMN source.
- A due interrupting boundary timer can cancel an active task and switch the
  same process instance onto its timeout path.
- The submit payload is attached when the requester completes the submit task.
- The manager and finance decision payloads are attached the same way.
- Lane assignment uses BPMN lane metadata together with the `lane_owners`
  mapping.
- User membership is validated against the tenant before user-scoped actions
  run.
- Command-level RBAC is enforced through `permission_target.command` for the
  API's currently covered workflow actions, including
  `process_definition.import`, `process.start`, `task.claim`,
  `task.complete`, `process.suspend`, `process.resume`, `process.retry`, and
  `process.terminate`.
- When the example uses the shared local m8flow database, the running process
  instance can be inspected in the m8flow UI at the same time as the terminal
  walkthrough.

## Parallel Review

`examples/parallel_review_poc.py` is a non-interactive walkthrough of a
purchase-order approval flow that exercises BPMN shapes the
conditional-approval POC does not cover:

- a **parallel gateway** (AND-split followed by AND-join),
- two **script tasks** (one before the split, one after the join),
- an **exclusive gateway** routed by a value the second script computes,
- three lanes (Requester, Finance, Compliance).

Run it with:

```bash
uv run python examples/parallel_review_poc.py
```

Flow shape:

```
Start
  -> ScriptTask:  set lane owners
  -> UserTask:    Submit Purchase Order   (Requester lane)
  -> ScriptTask:  Compute Total With Tax
  -> ParallelGateway (split)
        -> UserTask: Finance Review       (Finance lane)
        -> UserTask: Compliance Review    (Compliance lane)
  -> ParallelGateway (join)
  -> ScriptTask:  Determine Outcome
  -> ExclusiveGateway (final decision)
        -> UserTask: Notify Approved      -> End
        -> UserTask: Notify Rejected      -> End
```

The walkthrough prints what is pending after each step. The interesting
moment is right after Finance completes its review: the Compliance branch
is still pending and the requester sees no notification yet — the workflow
is correctly blocked at the join.

Change `SCENARIO` near the top of the file to try the rejection paths
(`finance_rejects`, `compliance_rejects`). The flow is also covered
end-to-end by `tests/test_parallel_review_poc.py` with all three scenarios
plus an explicit assertion that completing only one branch does **not**
release the join.

## Errors Demo

`examples/errors_demo.py` is a self-contained, non-interactive walkthrough
that demonstrates every public error class. It is the runnable counterpart
of the "Errors" section in [`api.md`](api.md).

Run it with:

```bash
uv run python examples/errors_demo.py
```

The script:

- creates an in-memory SQLite database, so no Postgres or Docker is required;
- seeds the minimum amount of data needed to trigger each error (two tenants,
  three users, a running process instance, a terminated process instance,
  a completed human task, and an unassigned human task);
- runs one public API call per error class and prints the error that was raised.

### Cases covered

| Case | Triggered through | Error |
| --- | --- | --- |
| Read a missing process instance | `GetProcessInstanceQuery` | `NotFoundError` (also `LookupError`) |
| User from another tenant lists pending tasks | `GetPendingTasksQuery` | `AuthorizationError` (also `PermissionError`) |
| User not assigned to a task completes it | `CompleteTaskCommand` | `AuthorizationError` (also `PermissionError`) |
| Suspend a terminated instance | `SuspendProcessInstanceCommand` | `InvalidStateError` (also `ValueError`) |
| Claim a completed task | `ClaimTaskCommand` | `InvalidStateError` (also `ValueError`) |
| Record an event with a bogus type | `RecordProcessInstanceEventCommand` | `ValidationError` (also `ValueError`) |
| Catch the same error at the base class | `RecordProcessInstanceEventCommand` | `BpmnCoreError` |

For each case the script asserts that the raised exception is **both** an
instance of the domain class **and** an instance of the matching builtin —
this is the contract documented in [`api.md`](api.md#errors).

### Sample output

```
--------------------------------------------------------------------------------
Case: NotFoundError
Reading a process instance that does not exist.
  raised:   NotFoundError: Process instance 999999 was not found for tenant tenant-errors-demo
  domain:   NotFoundError (also subclass of LookupError)
--------------------------------------------------------------------------------
Case: AuthorizationError (user not in tenant)
Listing pending tasks for a user that belongs to another tenant.
  raised:   AuthorizationError: User 3 does not belong to tenant tenant-errors-demo
  domain:   AuthorizationError (also subclass of PermissionError)
```

The script exits with a non-zero status if any case raises the wrong class
or returns successfully when an error was expected, so it doubles as a smoke
test for the error hierarchy.
