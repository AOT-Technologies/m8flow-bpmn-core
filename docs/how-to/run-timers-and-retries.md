# Run timers and scheduled retries

This guide shows how to run a scheduler loop that fires BPMN timers and
scheduled retries. The library stores due work in the `scheduler_job` table.
Your application decides when to wake up and calls one function,
`api.run_due_scheduler_jobs`, which does the rest.

The scheduler handles timer start events, recurring `timeCycle` timer starts,
intermediate timer catch events, interrupting timer boundary events, and
scheduled process retries. [About scheduling](../explanation/scheduling.md)
explains how each one is stored and run.

## Run an inline scheduler loop

To fire due jobs, call `run_due_scheduler_jobs` in a loop, each time inside
its own transaction:

```python
import time

from sqlalchemy import create_engine

from m8flow_core import api

engine = create_engine(DATABASE_URL)

while True:
    with engine.begin() as connection:
        processed = api.run_due_scheduler_jobs(
            connection,
            worker_id="inline-scheduler-1",
            tenant_id=TENANT_ID,
        )
    time.sleep(5 if processed == 0 else 1)
```

Replace the following:

- `DATABASE_URL`: the SQLAlchemy URL of the workflow database.
- `TENANT_ID`: the tenant to poll. Omit `tenant_id` to poll every tenant.

The function returns the number of jobs it processed, so the loop polls
faster while there's work. It processes at most `limit` jobs per call, 100 by
default. In tests, pass `now=` a timezone-aware `datetime` to decide what's due
without waiting for the real clock.

Run one poller per database, or one per tenant. The scheduler doesn't yet
coordinate several workers that claim the same rows.

## Handle scheduler failures

If one job in a batch fails, the library releases that job's lock and still
runs the rest of the batch. After the batch, it raises the original error if
one job failed, or a single `BpmnCoreError` that lists every failed job key if
several failed.

Because your application owns the transaction, decide whether to commit the
jobs that succeeded or roll back the whole batch. With `engine.begin()`, as in
the preceding example, an escaping error rolls back the batch.

## Schedule a retry for a failed process instance

A scheduled retry reopens the same process instance later. It doesn't create
a new one. To schedule a retry, follow these steps:

1. Move the process instance into `error` with `ErrorProcessInstanceCommand`,
   if it isn't there already. A failed service task does this for you.
2. Schedule the retry:

   ```python
   from datetime import UTC, datetime

   api.execute_command(
       connection,
       api.ScheduleProcessInstanceRetryCommand(
           tenant_id=TENANT_ID,
           process_instance_id=errored_instance.id,
           user_id=workflow_admin_user_id,
           retry_at=datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
       ),
   )
   ```

   The user needs the `process.retry` permission. Scheduling a second retry
   for the same instance updates the existing job instead of adding another.
3. Keep your scheduler loop running.

When the job is due, the library sets the instance back to `running`, reopens
its terminated tasks as `READY`, records a `process_instance_retried` event,
and deletes the job. If the error came from a service task, the library
reruns that service task straight away. If the instance has left `error` by
the time the job is due, the library deletes the job without retrying.

## Run the scheduler from Celery

To run the scheduler from Celery instead of an inline loop, schedule a Celery
beat task that calls `api.run_due_scheduler_jobs`. The stored jobs are the
same, so nothing else changes. `examples/celery_scheduler_poc.py` is a working
beat and worker setup, and
[Run the examples](run-the-examples.md#run-the-celery-timer-example) shows how
to start it.

The library doesn't ship its own Celery dispatcher yet.

## Related pages

- [About scheduling](../explanation/scheduling.md)
- [`run_due_scheduler_jobs` in the API reference](../reference/api.md#scheduler)
- [Run the examples](run-the-examples.md): the timer, cycle, boundary timer,
  and retry examples
