# Example Workflows

The repository ships four runnable examples:

| File | Purpose | Database |
| --- | --- | --- |
| `examples/conditional_approval_poc.py` | Interactive full approval flow (XOR gateway, DMN). | PostgreSQL |
| `examples/conditional_approval_rejection_poc.py` | Manager-rejection variant of the flow above. | PostgreSQL |
| `examples/parallel_review_poc.py` | Non-interactive purchase-order flow that exercises a parallel gateway and script tasks. | In-memory SQLite |
| `examples/errors_demo.py` | Non-interactive walk through every public error class. | In-memory SQLite |

The two conditional-approval examples are interactive walkthroughs that
exercise an exclusive gateway with a DMN-driven branch. The parallel-review
example covers the BPMN shapes the conditional-approval flow does not —
parallel gateway (AND-split + AND-join) and Python script tasks. The errors
demo is a quick, dependency-free way to see each `BpmnCoreError` subclass
being raised by the services.

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

The rejection variant changes the manager decision to `Rejected`, so the flow
ends before the Finance lane is activated.

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
resolve to the deployed model in the UI.

- PowerShell:

  `.\examples\conditional_approval_poc.ps1`

  `.\examples\conditional_approval_rejection_poc.ps1`

- Bash:

  `bash examples/conditional_approval_poc.sh`

  `bash examples/conditional_approval_rejection_poc.sh`

Add `-UseDocker` or `--docker` to force the temporary container. Add
`-KeepContainer` or `--keep-container` if you want to leave the container
running after the example exits.

## What The Example Demonstrates

- A workflow can be imported from BPMN and DMN source.
- The submit payload is attached when the requester completes the submit task.
- The manager and finance decision payloads are attached the same way.
- Lane assignment uses BPMN lane metadata together with the `lane_owners`
  mapping.
- User membership is validated against the tenant before user-scoped actions
  run.

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
