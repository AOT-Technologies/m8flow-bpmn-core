# Example Workflows

The repository ships three runnable examples:

| File | Purpose | Database |
| --- | --- | --- |
| `examples/conditional_approval_poc.py` | Interactive full approval flow. | PostgreSQL |
| `examples/conditional_approval_rejection_poc.py` | Manager-rejection variant of the flow above. | PostgreSQL |
| `examples/errors_demo.py` | Non-interactive walk through every public error class. | In-memory SQLite |

The conditional-approval examples are interactive walkthroughs that exercise
the public API end to end. The errors demo is a quick, dependency-free way to
see each `BpmnCoreError` subclass being raised by the services.

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

The repository also includes shell launchers that use a local Postgres instance
when one is reachable and otherwise start a temporary Docker container.

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
