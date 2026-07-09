# Sample App Docs

This folder contains documentation for the `sample_app/` host application that
uses `m8flow-bpmn-core` as a dependency.

## Purpose

The sample app exists to prove that a thin application can:

- consume `m8flow-bpmn-core` from a built wheel
- own its own Alembic migration history
- store workflow definitions in the same database tables the library expects
- start workflows, claim tasks, complete tasks, and inspect events through the
  library API
- run timer-based workflows through an app-managed scheduler loop
- execute service tasks through the shared connector-proxy direction used by
  m8flow

## Main Components

- `src/m8flow_sample_app/app.py`
  - Flask app factory, startup migrations, seed wiring, scheduler startup.
- `src/m8flow_sample_app/db.py`
  - SQLAlchemy engine/session helpers and programmatic Alembic runner.
- `src/m8flow_sample_app/seed.py`
  - Static tenants, users, lane owners, and default secrets.
- `src/m8flow_sample_app/views/`
  - HTML routes for process definitions, workflow start, tasks, process
    instances, and secrets.
- `src/m8flow_sample_app/workflows/`
  - Thin wrappers around the library public API.
- `src/m8flow_sample_app/scheduler.py`
  - Host-side scheduler poller for timer jobs.
- `fixtures/sample_app_demo.bpmn`
  - Reimbursement workflow with conditional Finance review and SMTP service
    task.
- `fixtures/sample_app_review_timeout_escalation.bpmn`
  - Manual-review workflow with an interrupting boundary timer that escalates
    to a supervisor lane.

## Built-In Workflows

### Reimbursement Demo

- Starts with `Submit Reimbursement Request`
- Routes amounts over `1000` through `Finance Review`
- Skips final review when Finance rejects
- Sends the outcome through the SMTP connector-proxy service task

### Timeout Escalation Demo

- Starts with `Review Submitted Request`
- Keeps the initial review in the `Operations` lane
- Uses an interrupting boundary timer with `PT2M`
- If the review is still open after two minutes, cancels that task and creates
  `Supervisor Review` in the `Supervisor` lane

## Authentication And Audit Modes

The sample app supports two operating modes:

- standalone mode
  - local tenant/user picker
  - useful for isolated local runs
- shared audit mode
  - provisions users in the shared Keycloak realm
  - aligns tenant ids to Keycloak organization ids
  - publishes BPMN files into the local m8flow backend catalog so models are
    visible in m8flow UI

## Scheduler

The sample app uses a simple in-process polling loop for timers.

- It is started from `create_app()` during application startup.
- It runs in a daemon thread inside the sample app process.
- Every poll cycle it opens a DB session and calls
  `api.run_due_scheduler_jobs(...)`.
- The app does not decide which timer fired. The library reads persisted
  scheduler rows and executes whichever jobs are due.

See [scheduler.md](./scheduler.md) for the exact polling flow and settings.

## Useful Settings

- `M8FLOW_SAMPLE_APP_DATABASE_URL`
- `M8FLOW_SAMPLE_APP_M8FLOW_AUDIT_MODE`
- `M8FLOW_SAMPLE_APP_CONNECTOR_PROXY_BASE_URL`
- `M8FLOW_SAMPLE_APP_CONNECTOR_PROXY_TIMEOUT_SECONDS`
- `M8FLOW_SAMPLE_APP_SCHEDULER_ENABLED`
- `M8FLOW_SAMPLE_APP_SCHEDULER_POLL_SECONDS`
- `M8FLOW_SAMPLE_APP_SCHEDULER_BATCH_LIMIT`
- `M8FLOW_SAMPLE_APP_SCHEDULER_WORKER_ID`

## Related Docs

- [../README.md](../README.md)
- [scheduler.md](./scheduler.md)
- [../../doc/sample_app.md](../../doc/sample_app.md)
