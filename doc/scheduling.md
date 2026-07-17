# Scheduling Architecture

This document describes the internal scheduling architecture for timer events,
retry scheduling, and optional Celery-backed execution in
`m8flow_bpmn_core`.

This is **not** yet a stable public API contract. The stable public surface
remains `m8flow_bpmn_core.api` and is documented in [`api.md`](api.md).

## Goals

- Persist due work in the database so scheduled state survives restart.
- Keep timer/retry semantics inside the library rather than pushing BPMN-aware
  logic into the host application.
- Support both:
  - a caller-owned inline poller for non-Celery deployments
  - a Celery-backed execution path that matches m8flow's architecture

## Core Scheduler Record

The internal scheduler foundation is the `scheduler_job` table and
`SchedulerJobModel`.

Each row stores:

- `job_key`: tenant-scoped deduplication key for a logical scheduled action
- `job_type`: one of:
  - `intermediate_timer`
  - `process_retry`
  - `timer_start`
- `run_at_in_seconds`: the next due time
- `process_instance_id` or `bpmn_process_definition_id`: the target scope
- `payload_json`: timer- or retry-specific metadata
- `locked_by` / `locked_at_in_seconds`: worker-claim fields for future polling
  and Celery dispatch flows

The `job_key` is intentionally generic so later steps can reschedule the same
logical job without creating duplicate rows.

### `scheduler_job` Schema And ORM Model

The ORM model lives in
[`src/m8flow_bpmn_core/models/scheduler_job.py`](../src/m8flow_bpmn_core/models/scheduler_job.py)
as `SchedulerJobModel`.

It is a tenant-scoped table with one row per persisted logical scheduled job.

| Column | Type | Null? | Notes |
| --- | --- | --- | --- |
| `id` | integer | no | Primary key. |
| `m8f_tenant_id` | string | no | Foreign key to `m8flow_tenant.id`; all scheduler reads and writes stay tenant-scoped. |
| `job_key` | string(255) | no | Stable deduplication key for the logical job inside one tenant. |
| `job_type` | string(50) | no | Indexed discriminator. Current values: `intermediate_timer`, `process_retry`, `timer_start`. |
| `process_instance_id` | integer | yes | Foreign key to `process_instance.id`. Used for jobs that belong to a specific running instance, such as intermediate timers and retries. |
| `bpmn_process_definition_id` | integer | yes | Foreign key to `bpmn_process_definition.id`. Used for definition-scoped jobs, especially timer-start events. |
| `locked_by` | string(255) | yes | Worker identifier for claimed jobs. `NULL` means the row is currently unclaimed. |
| `locked_at_in_seconds` | bigint | yes | Timestamp for when the claim happened. V1 stores it for coordination and future recovery logic. |
| `run_at_in_seconds` | bigint | no | Indexed due timestamp used by the poller or a future Celery dispatcher. |
| `payload_json` | JSON | no | Job-specific metadata. For example, timer descriptors or retry context. Defaults to an empty object. |
| `updated_at_in_seconds` | bigint | no | Last mutation timestamp for the row. |
| `created_at_in_seconds` | bigint | no | Initial insert timestamp for the row. |

The model also exposes two SQLAlchemy relationships:

| Relationship | Target table | Purpose |
| --- | --- | --- |
| `process_instance` | `process_instance` | Lets instance-scoped scheduler jobs follow cascade deletion when the process instance is removed. |
| `bpmn_process_definition` | `bpmn_process_definition` | Lets definition-scoped scheduler jobs follow cascade deletion when the imported definition is removed. |

Important constraints and validation rules:

- Unique constraint `uq_scheduler_job_tenant_job_key` enforces one
  `job_key` per tenant. This is what allows rescheduling to update the same row
  instead of inserting duplicates.
- `job_type` is validated against `SchedulerJobType` when the ORM model is
  written. Invalid values raise `ValidationError`.
- `process_instance_id` and `bpmn_process_definition_id` are intentionally
  nullable because different job types use different scopes.
- `locked_by` and `locked_at_in_seconds` are persisted even in the inline
  poller path so the same schema can support future multi-worker or
  Celery-dispatch coordination.

In practice, the table behaves like a small durable queue:

- the workflow runtime inserts or refreshes rows when it discovers future work
- the scheduler runtime lists rows whose `run_at_in_seconds` is due
- the runtime claims a row by filling `locked_by`
- successful execution either deletes the row or rewrites it with the next due
  time, depending on the timer/retry semantics

### Internal Helper Functions

The low-level database helpers live in
[`src/m8flow_bpmn_core/services/scheduler_jobs.py`](../src/m8flow_bpmn_core/services/scheduler_jobs.py).
They are internal building blocks used by the runtime services; callers should
not treat them as stable public API.

#### Stable Job Keys

`build_scheduler_job_key(...)` creates a deterministic key from:

- `job_type`
- optional `process_instance_id`
- optional `bpmn_process_definition_id`
- optional `qualifier`

The format is pipe-delimited:

- instance-scoped example:
  `intermediate_timer|pi:12|q:TimerCatch_1`
- definition-scoped example:
  `timer_start|pd:34|q:StartEvent_1`

The helper normalizes `job_type` through `SchedulerJobType`, trims the
qualifier, and rejects blank qualifiers. It also rejects empty scope
definitions: at least one of process instance, process definition, or a
non-blank qualifier must be present.

That matters for two reasons:

- it prevents accidental collisions between unrelated scheduler rows
- it lets later workflow saves recompute the exact same key and upsert the same
  logical job row

#### Upsert

`upsert_scheduler_job(...)` is the write-side primitive used whenever the
runtime wants to persist or reschedule work.

How it works:

1. It normalizes the `job_type`.
2. It resolves the effective timestamp for `updated_at_in_seconds`
   and defaults to the current time when the caller does not pass one.
3. It looks up an existing row by the tenant-scoped unique key:
   `m8f_tenant_id + job_key`.
4. If no row exists, it inserts a new `SchedulerJobModel`.
5. If a row already exists, it updates the existing row in place.
6. In both cases it flushes the session before returning the ORM object.

When an existing row is updated, the helper intentionally resets
`locked_by` and `locked_at_in_seconds` back to `NULL`. That makes a rescheduled
job visible again to the next scheduler pass instead of leaving it stuck in a
claimed state from a previous execution attempt.

The mutable fields refreshed by upsert are:

- `job_type`
- `process_instance_id`
- `bpmn_process_definition_id`
- `run_at_in_seconds`
- `payload_json`
- `updated_at_in_seconds`

`created_at_in_seconds` is preserved unless the caller explicitly overrides it.

#### Delete

`delete_scheduler_job(...)` removes a row by `tenant_id + job_key` and returns
`True` when a row was actually deleted.

The tenant predicate is important. A caller cannot remove another tenant's
scheduled row even if it somehow knows the `job_key`. This helper is used when
the runtime determines that a previously scheduled logical job no longer
exists, for example because a timer is no longer waiting or a retry job has
been fully consumed.

#### Due-Job Listing

`list_due_scheduler_jobs(...)` is the read-side primitive for the poller and
runtime claim flow.

How it decides what is due:

- `run_at_in_seconds <= now`
- `locked_by IS NULL`
- optional `tenant_id` filter if the caller wants only one tenant

How it orders rows:

- first by `run_at_in_seconds`
- then by `id`

That ordering keeps due-job scans deterministic when multiple rows share the
same due second.

The helper also enforces `limit > 0` and raises `ValidationError` otherwise.
The limit keeps each scheduler pass bounded; V1 defaults to `100`.

This helper only lists candidates. Claiming and execution happen in the
runtime layer, not here. That split keeps the storage helper simple while still
supporting both:

- inline polling loops that execute the row immediately
- future Celery dispatchers that list and claim rows before handing them to
  worker transport

### Scheduled Retry Lifecycle

Scheduled retry is intentionally modeled as a scheduler concern on top of the
existing process-instance lifecycle, not as a separate retry-only runtime.

The write path is:

1. A host application or operator moves a process instance into `error`.
2. The host application schedules a retry with
   `ScheduleProcessInstanceRetryCommand`.
3. The library persists or refreshes one `process_retry` row keyed to that
   process instance.

The persisted row is process-instance scoped:

- `job_type = process_retry`
- `process_instance_id = <current instance id>`
- `bpmn_process_definition_id = <definition id>`
- `run_at_in_seconds = <next retry due time>`
- `payload_json = {"requested_by_user_id": ..., "scheduled_at_in_seconds": ...}`

At execution time, `api.run_due_scheduler_jobs(...)` claims the due row and the
runtime does the following:

1. Reload the current process instance by `tenant_id + process_instance_id`.
2. If the process instance is no longer in `error`, delete the stale row and
   stop.
3. If the instance is still in `error`, call the normal
   `retry_process_instance(...)` service with the stored `requested_by_user_id`.
4. That service returns the same process instance from `error` to `running`,
   clears `end_in_seconds`, reopens terminated runtime tasks, and resets
   terminated human tasks back to `READY`.
5. The retry service also records `process_instance_retried` and deletes the
   consumed `process_retry` scheduler row.

The important behavioral consequence is that delayed retry does **not** create
a new process instance. It reactivates the existing one.

That is why the retry POC verifies both of these after the due row fires,
before it claims and completes the reopened task:

- the process instance id is unchanged
- the original human task id is visible again in the pending worklist

This lifecycle is the same regardless of whether due rows are driven by:

- an inline poller that calls `api.run_due_scheduler_jobs(...)`
- a future Celery dispatcher/worker integration that claims the same rows
  before executing them

### Boundary Timer Lifecycle

Interrupting timer boundary events reuse the same persisted
`intermediate_timer` job type as intermediate catch events. The difference is
what the workflow refresh does after the due row is executed.

The write path is:

1. A normal process instance is started through the public API.
2. Workflow persistence discovers the waiting timer boundary task through the
   same timer scan that handles other non-start waiting timers.
3. The runtime stores or refreshes one instance-scoped `intermediate_timer`
   row keyed to that process instance.

At execution time, `api.run_due_scheduler_jobs(...)` claims the due row and
the runtime does the following:

1. Reload the current process instance by `tenant_id + process_instance_id`.
2. Restore the serialized workflow and refresh waiting tasks.
3. Re-run the workflow so the boundary timer becomes due inside Spiff.
4. Persist the refreshed workflow state and sync the `task` rows.
5. Close any still-open `human_task` rows whose underlying workflow task is no
   longer active. For an interrupting boundary timer, that means the attached
   task is closed as `CANCELLED`.
6. Materialize any newly READY manual tasks on the timeout path.
7. Refresh or delete the instance-scoped `intermediate_timer` row depending on
   whether more waiting timers remain.

The important behavioral consequence is that a due boundary timer does **not**
create a new process instance. It mutates the current one in place:

- the attached human task is cancelled and leaves the pending worklist
- the timeout-path task becomes READY on that same process instance

This lifecycle is the same regardless of whether due rows are driven by:

- an inline poller that calls `api.run_due_scheduler_jobs(...)`
- a future Celery dispatcher/worker integration that claims the same rows
  before executing them

## Execution Modes

The library is designed so the persisted job model is the same regardless of
how scheduled work is executed.

### Inline Poller

In a non-Celery deployment, the host application runs a small loop or worker
process that periodically asks the library to find and execute due jobs through
`api.run_due_scheduler_jobs(...)`.

The host application is responsible for waking up periodically. The library is
responsible for:

- deciding which jobs are due
- claiming due jobs for the current worker invocation
- creating timer-started process instances
- restoring workflow state
- refreshing waiting timers
- advancing the workflow
- retrying errored process instances through the normal retry lifecycle
- rescheduling the next due timer, if any

Current V1 note:

- the inline path is intentionally simple and should be treated as a
  single-poller execution model
- the same persisted rows can later feed a Celery dispatcher without changing
  the storage model
- if one claimed due job fails, the runtime releases that job's lock, keeps
  executing the other claimed jobs from the same batch, and only raises after
  the batch finishes
- if the batch had one failure, that original scheduler error is re-raised;
  if the batch had multiple failures, the runtime raises one summary
  `BpmnCoreError` with the failed job keys and error details

### Celery-Backed Execution

In a Celery-backed deployment, a host-owned worker arrangement can drive the
same persisted jobs through Celery instead of through an inline loop.

This matches the m8flow architectural direction:

- scheduled state persists in the database
- Celery provides worker transport and isolation
- tenant/session context is restored by the integrating application

The repository now includes a minimal example-level worker in
[`examples/celery_scheduler_poc.py`](../examples/celery_scheduler_poc.py).
That worker does not add a new library-owned dispatcher API. It simply uses
Celery beat to call the existing public polling entrypoint
`api.run_due_scheduler_jobs(...)` on a schedule. This is enough to demonstrate
compatibility with the same Redis broker m8flow already uses. On Windows, the
PowerShell launcher starts `celery beat` as a separate helper process because
Celery does not support `worker --beat` there.

## Current Status

Implemented so far:

- `scheduler_job` schema and ORM model
- internal helper functions for stable job keys, upsert, delete, and due-job
  listing
- automatic synchronization of waiting intermediate catch and boundary timers
  into scheduler jobs whenever workflow state is persisted
- due-job claiming and batch-continuing inline execution for waiting
  intermediate catch and boundary timers
- timer-start synchronization from imported process definitions
- due-job execution for timer-started process instances
- recurring timer-start rescheduling for finite `timeCycle` definitions, with
  cleanup after the final occurrence
- scheduled retry persistence for errored process instances
- due-job execution for scheduled process retries
- public polling entrypoint: `api.run_due_scheduler_jobs(...)`
- an example-level Celery beat/worker poller that drives
  `api.run_due_scheduler_jobs(...)` with the same persisted scheduler rows

Not implemented yet:

- production-grade multi-worker claim coordination
- generic reminder and escalation job types:
  BPMN timeout paths such as interrupting boundary-timer escalations are
  supported, but there is still no separate generic reminder/escalation job
  framework.
- first-class library-owned Celery adapter and dispatcher hooks beyond the
  current example-level beat/worker integration
