# Example Workflows

The repository includes two interactive workflow walkthroughs that exercise the
public API end to end.

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
