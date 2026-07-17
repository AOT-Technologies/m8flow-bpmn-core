# Scheduler

This document explains how timer polling works in `sample_app/`.

## Overview

The sample app does not use Celery for timers. It runs a simple host-managed
polling loop inside the web-app process.

That polling loop lives in
`sample_app/src/m8flow_sample_app/scheduler.py` and is started from
`sample_app/src/m8flow_sample_app/app.py`.

## Startup Flow

When the app starts:

1. `create_app()` loads settings.
2. It runs migrations.
3. It seeds tenants, users, permissions, and default secrets.
4. If `scheduler_enabled` is `true`, it creates a
   `SampleAppSchedulerPoller`.
5. The poller starts a daemon background thread.

## Polling Loop

The background thread runs `_run_loop()` in `SampleAppSchedulerPoller`.

Each iteration:

1. Reuses a SQLAlchemy engine for the configured database.
2. Opens a session through `session_scope(...)`.
3. Installs the sample-app service-task registry with
   `api.service_task_registry_scope(...)`.
4. Calls `api.run_due_scheduler_jobs(...)`.
5. Sleeps for `poll_seconds`.

The app itself is not deciding which timer is due. The library does that by
reading the persisted `scheduler_job` rows from the database.

## What Gets Polled

The loop is generic. It does not only poll one workflow.

It allows the library to process any due scheduler jobs for the configured
database, including:

- intermediate timer jobs
- boundary timer jobs
- timer-start jobs
- scheduled retry jobs

## Why The Service Task Registry Is Installed

Timer advancement can continue a workflow into service-task execution.

Because of that, the scheduler loop wraps the cycle in the same
service-task-registry scope used by request-driven workflow actions. This keeps
connector behavior consistent whether the workflow advances:

- from a user claiming or completing a task
- or from a timer firing in the background poller

## Configuration

The relevant settings are:

- `M8FLOW_SAMPLE_APP_SCHEDULER_ENABLED`
  - default: `true`
- `M8FLOW_SAMPLE_APP_SCHEDULER_POLL_SECONDS`
  - default: `1.0`
- `M8FLOW_SAMPLE_APP_SCHEDULER_BATCH_LIMIT`
  - default: `100`
- `M8FLOW_SAMPLE_APP_SCHEDULER_WORKER_ID`
  - default: `sample-app-inline-scheduler`

## Current Tradeoffs

This is intentionally simple.

- It is single-process and in-process.
- It is enough for demonstrating host-app timer support.
- It is not a production-grade multi-worker scheduler.
- If the sample app process is down, no polling happens until it starts again.

The important part for this sample app is that timer state is persisted in the
database, so the scheduler can resume work after restart.
