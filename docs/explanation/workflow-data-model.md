# About the workflow data model

m8flow-core stores a running workflow in a handful of tables, each with one
job. This page explains how they divide the work, and why 1.0 settled on this
shape: work items for claim state, split event types, timezone-aware
timestamps, and tenant-scoped payloads.

## Three kinds of task state

A human task in a running process has three kinds of state, and each lives in
one place:

- **Execution state** belongs to SpiffWorkflow. The `task` table stores it
  with SpiffWorkflow's own state names, such as `READY`, `COMPLETED`, `ERROR`,
  and `CANCELLED`, and the runtime compares them through SpiffWorkflow's
  `TaskState` enum rather than bare strings.
- **Claim state** belongs to the library. The `work_item` table holds who may
  work on the task, who claimed it, who completed it, and its status:
  `READY`, `CLAIMED`, `COMPLETED`, `TERMINATED`, `CANCELLED`, or `ERROR`. The
  potential owners are rows in `work_item_user`.
- **Display details**, such as the task's name, title, type, lane, form, and
  process name, belong to the task, task definition, and process definition
  models.

A work item doesn't copy display details. It reads them through properties
such as `task_title` and `lane_name`. Keeping each fact in one table means a
claim can't disagree with the workflow about what the task is called or
where it sits.

Claims are also safe under concurrency. Claiming uses a compare-and-set on the
work item row, so when two users claim the same task at once, one succeeds and
the other gets `InvalidStateError`.

## Who can work on a task

A work item belongs to a lane. Its potential owners come from the definition's
`lane_owners` mapping, or from a script task that sets it, matched against
users in the tenant. Lane groups are qualified by tenant, so two tenants with
a lane named `Finance` never share an owner list.

`lane_owners` is optional. With no owners, the process still starts and the
work item waits as `READY`. A host application that syncs group membership
from a directory can then call `assign_pending_tasks_for_user` to make a user
a potential owner of the waiting work in their lanes. That doesn't claim
anything; the user still claims the work item themselves.

## Events have a category

The event history is one table, `process_instance_event`, with two
vocabularies:

- `ProcessLifecycleEventType` for things that happen to the instance, such as
  `process_instance_created` or `process_instance_retried`.
- `TaskEventType` for things that happen to a task, such as `task_completed`
  or `task_failed`.

Every event stores a non-null `category`, `process` or `task`, derived from
its type. A reader can filter one kind without knowing every event name, and
the event string values are the same ones m8flow has always stored, so
existing history stays readable.

## Timestamps are datetimes

Every timestamp is a timezone-aware UTC `datetime`, in the API and in the
database. Epoch-second integers are gone from both. Native date types remove
a class of unit and timezone mistakes, and they avoid the year-2038 limit of
32-bit epoch values.

SpiffWorkflow still serializes timer due times as epoch numbers inside its
own workflow data. The library converts them to `datetime` before it writes
`scheduler_job.run_at`, so they never reach a column or the API.

## Payloads are owned by a tenant

Process and task data are stored as JSON payloads in `json_data`, deduplicated
by a hash of the content. In 0.x, the hash alone was the key, so two tenants
with identical data shared one row. In 1.0, the key is the tenant and the hash
together. Every lookup includes the tenant, so one tenant's data can never be
read through another tenant's process, even when the payloads are equal.

## Permissions name exact resources

A permission targets a command and an exact resource: a resource type, such as
`process_model`, and an optional resource ID. 0.x matched URI patterns such as
`/tasks/%` instead. Exact pairs make a grant unambiguous to read and to check,
and conflict-safe row creation means two requests that create the same grant
at once don't produce duplicates.
[About authorization and tenancy](authorization-and-tenancy.md) explains the
matching rules.

## Why the redesign happened

The 0.x models and authorization code contained structures adapted from
spiff-arena. The 1.0 data model replaces them with an independent design, and
fixes tenant isolation, timestamp, and concurrency problems on the way.
[About the clean-room review](clean-room-review.md) records how that was
checked.

## Related pages

- [Database schema](../reference/database-schema.md)
- [Upgrade to 1.0](../how-to/upgrade-to-1.0.md)
- [About the architecture](architecture.md)
