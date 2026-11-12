<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Troubleshoot common problems

This guide lists problems that come up when you embed m8flow-core, what
causes each one, and how to fix it. Find the symptom you see, then follow its
fix. If your problem isn't here, see [Get more help](#get-more-help).

## Changes disappear after a call returns

**Cause:** you passed a `Connection` and never committed. With a
`Connection`, the library opens a temporary `Session` on it and never commits
or rolls back. The transaction is yours.

**Fix:** run the calls inside `engine.begin()`, which commits when the block
ends without an error, or call `connection.commit()` yourself. See
[Embed the library in your application](embed-in-your-application.md#choose-a-session-or-a-connection).

## `TypeError` from `execute_command` or `execute_query`

**Cause:** you passed a query to `execute_command`, a command to
`execute_query`, or an object that is neither.

**Fix:** send writes, the `*Command` classes, to `execute_command`, and reads,
the `*Query` classes, to `execute_query`.

## `AuthorizationError` on a call that takes a `user_id`

**Cause:** one of the following:

- The user isn't a member of the tenant in `tenant_id`.
- The user has no role that grants the command's permission, such as
  `process.start` or `task.claim`.
- For a claim or completion, the user isn't a potential owner of the work
  item, or another user holds the claim.

**Fix:** check that the user belongs to the tenant, then give them a role
with `ensure_v1_role`, as described in
[Customize authorization](customize-authorization.md). Use
`GetPendingTasksQuery` with the user's `user_id` to see which work items they
can claim. The error message names the check that failed.

## A user doesn't see a task they expect

**Cause:** one of the following:

- The task's lane has no entry in `properties_json["lane_owners"]`, so the
  work item waits with no potential owners.
- An owner in `lane_owners` matches no user in the tenant. The library skips
  it and logs a warning from the `m8flow_core.services.lane_assignment`
  logger.
- The task has no lane, so it went to the user who started the instance.

**Fix:** check your application's logs for the lane owner warning. Correct the
owners in `lane_owners` when you import the definition, or list the lane in
`initiator_lanes`. For work items that already exist, sync the user into the
lane group and call `api.assign_pending_tasks_for_user`. See
[Assign tasks through lanes](embed-in-your-application.md#assign-tasks-through-lanes).

## `ValidationError` when you import a definition

**Cause:** the BPMN or DMN XML, or `properties_json`, is invalid. Common
cases:

- The XML has more than one executable process and you didn't say which one
  to use with `bpmn_process_id`.
- An `initiator_lanes` entry names a lane that isn't in the BPMN, or also has
  a key in `lane_owners`.
- The XML isn't well-formed, or the parser rejects the model.

**Fix:** read the error message, which names the problem. Check the model
against [BPMN support](../reference/bpmn-support.md) and
[Limitations](../reference/limitations.md).

## `InvalidStateError` from a task or lifecycle command

**Cause:** the instance or work item is in a state that doesn't allow the
command, such as claiming a completed work item, resuming an instance that
isn't suspended, or retrying an instance that isn't in `error`.

**Fix:** read the current state with `GetProcessInstanceQuery` or
`GetPendingTasksQuery` before you act. The
[lifecycle commands table](../reference/api.md#lifecycle-commands) lists the
allowed transitions.

## Timers never fire

**Cause:** nothing runs the scheduler. Timer and retry jobs are rows in the
database; the library doesn't start a background thread to run them.

**Fix:** call `api.run_due_scheduler_jobs` in a loop, each call in its own
transaction, as shown in
[Run timers and scheduled retries](run-timers-and-retries.md#run-an-inline-scheduler-loop).
If you pass `tenant_id`, the call runs only that tenant's jobs. Run one
scheduler per database, or one per tenant, because several workers don't yet
coordinate.

## The scheduler raises an error and finished jobs roll back

**Cause:** one or more jobs in the batch failed. The scheduler runs the whole
batch, then raises. With `engine.begin()`, the error rolls back the jobs that
succeeded too.

**Fix:** decide whether to keep the successful jobs. To keep them, catch the
error and commit. See
[Handle scheduler failures](run-timers-and-retries.md#handle-scheduler-failures).

## A failed service task leaves no trace after rollback

**Cause:** in most workflows, the library saves a service task failure in a
separate transaction so that it survives your rollback. In these cases, the
failure is part of your transaction instead, so a rollback removes it:

- The service task ran after `CompleteTaskCommand`.
- The workflow uses BPMN signals.
- The failure happened in a compensation handler.
- The database is SQLite, whose locking can't run the separate writer
  reliably.

**Fix:** catch `ServiceTaskExecutionError` or `CompensationExecutionError`,
commit to keep the instance in `error`, then retry it with
`RetryProcessInstanceCommand` or `ScheduleProcessInstanceRetryCommand`. Use
PostgreSQL in production. See
[About service task execution](../explanation/service-task-execution.md#the-failure-transaction-rule).

## A signal doesn't reach another process instance

**Cause:** a throw delivers to catchers in the same instance right away.
Deliveries to other instances and to signal start events wait in a queue
until your application runs the delivery loop. Either nothing runs the loop,
or its transaction rolled back.

**Fix:** run `RunSignalDeliveriesCommand` for each tenant in a loop and commit
each call. Signals never cross tenants. A throw that no process is waiting
for succeeds and does nothing. To see what happened, read the deliveries with
`ListSignalDeliveriesQuery`. See
[Run durable deliveries](publish-signals.md#run-durable-deliveries).

## `AuthorizationError` when a manager publishes a signal

**Cause:** the manager or admin role was created before the
`signal.publish` grant existed.

**Fix:** re-run `ensure_v1_role` for the tenant's existing `manager` and
`admin` roles.

## A table is missing, or `alembic current` isn't at the head

**Cause:** the database hasn't had every migration applied. For example, a
database created with a 1.0.0 beta has no signal or compensation tables.

**Fix:** back up the database, then run `uv run alembic upgrade head`. Then
run `uv run alembic current` and check that it reports
`m8f_671_compensation`. To find differences between the tables and the
models, run `uv run alembic check`. See
[Migrate the database](upgrade-to-1.0.md#migrate-the-database).

## `ModuleNotFoundError` for a database driver

**Cause:** the driver for your database isn't installed. Only SQLite works
without an extra.

**Fix:** install the matching extra, such as `pip install
"m8flow-core[postgresql]"` or `pip install "m8flow-core[mysql]"`, and use a
matching URL, such as `postgresql+psycopg://...`. See
[Configuration](../reference/configuration.md#library).

## Get more help

If none of these fixes the problem, do the following:

- Run the closest [example](run-the-examples.md) to see a working setup.
- Read [SUPPORT.md](../../SUPPORT.md) for where to ask questions.
- Search the [issue tracker](https://github.com/AOT-Technologies/m8flow-core/issues),
  and open an issue with the error message, the library version, and the
  database you use.
