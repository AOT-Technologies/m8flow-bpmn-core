# Sample app scheduler

The sample app fires timers with a polling loop that runs inside the web app
process, not with Celery. This page explains how the loop starts, what each
pass does, and its trade-offs.

The loop is in `src/m8flow_sample_app/scheduler.py`, and `app.py` starts it.

## Startup

When the app starts, it does the following:

1. `create_app()` loads the settings.
2. It runs the migrations.
3. It seeds tenants, users, permissions, and default secrets.
4. If `M8FLOW_SAMPLE_APP_SCHEDULER_ENABLED` is `true`, it creates a
   `SampleAppSchedulerPoller`, which starts a daemon background thread.

## Each pass

The thread runs `SampleAppSchedulerPoller._run_loop()`. Each pass does the
following:

1. Reuses one SQLAlchemy engine for the configured database.
2. Opens a session through `session_scope(...)`.
3. Installs the app's service task registry with
   `api.service_task_registry_scope(...)`.
4. Calls `api.run_due_scheduler_jobs(...)`.
5. Sleeps for the poll interval.

The app doesn't decide which timer is due. The library reads the stored
`scheduler_job` rows and runs whatever is due: timer starts, intermediate and
boundary timers, and scheduled retries, for any workflow.

## Why it installs the service task registry

A timer can advance a workflow into a service task. The loop installs the same
registry that request handlers use, so a connector behaves the same whether
the workflow moved because a user completed a task or because a timer fired.

## Settings

The loop reads the following settings:

| Variable | Default |
|---|---|
| `M8FLOW_SAMPLE_APP_SCHEDULER_ENABLED` | `true` |
| `M8FLOW_SAMPLE_APP_SCHEDULER_POLL_SECONDS` | `1.0` |
| `M8FLOW_SAMPLE_APP_SCHEDULER_BATCH_LIMIT` | `100` |
| `M8FLOW_SAMPLE_APP_SCHEDULER_WORKER_ID` | `sample-app-inline-scheduler` |

## Trade-offs

The loop is deliberately minimal:

- It runs in one process, inside the web app.
- It's enough to demonstrate timers in a host application, but it isn't a
  production multi-worker scheduler.
- While the app is down, nothing is polled.

Because timer state is stored in the database, the loop picks up any overdue
jobs when the app starts again.

## Related pages

- [Sample app architecture](README.md)
- [Run timers and scheduled retries](../../docs/how-to/run-timers-and-retries.md)
- [About scheduling](../../docs/explanation/scheduling.md)
