# AGENT.md

## Purpose

`m8flow-bpmn-core` is an in-process Python library built on top of
`SpiffWorkflow`. The goal is to provide a reusable BPMN/DMN workflow core for
M8Flow in the same general spirit as an embeddable Camunda-style library:
callers import Python APIs directly and run workflow logic inside their own
application process.

This repository is not an HTTP service. Do not design changes around REST
controllers, request handlers, or transport-layer DTOs unless the project
direction explicitly changes.

## Source Of Truth

Read these first before changing behavior:

- `doc/api.md`: public API contract and error semantics
- `doc/usage.md`: expected integration pattern for callers
- `doc/examples.md`: runnable examples and supported BPMN patterns
- `doc/package.md`: build and packaging workflow
- `doc/gaps.md`: intentionally out-of-scope areas

If code and docs disagree, treat that as a bug and bring them back into sync.

## Architecture

- `src/m8flow_bpmn_core/api.py`
  - Stable public entrypoint for downstream consumers.
  - Re-exports commands, queries, errors, enums, and service functions.
- `src/m8flow_bpmn_core/application/commands.py`
  - Write-side command dataclasses.
- `src/m8flow_bpmn_core/application/queries.py`
  - Read-side query dataclasses.
- `src/m8flow_bpmn_core/application/dispatcher.py`
  - `execute_command(...)` and `execute_query(...)`.
  - Accepts either a SQLAlchemy `Session` or `Connection`.
  - When given a `Connection`, it opens a temporary `Session` and does not
    commit or roll back. The caller owns the transaction boundary.
- `src/m8flow_bpmn_core/services/`
  - Business logic by concern: process definitions, process instances, tasks,
    tenant-user checks, and SpiffWorkflow runtime integration.
- `src/m8flow_bpmn_core/models/`
  - SQLAlchemy ORM models. Public API returns these models directly.
- `tests/fixtures/`
  - BPMN and DMN fixtures used by tests and examples.
- `examples/`
  - Runnable reference flows, including conditional approval, parallel review,
    and error demonstrations.

## Public API Invariants

Preserve these unless you are intentionally making a contract change:

- `m8flow_bpmn_core.api` is the public surface.
- Commands and queries are separate concepts.
  - Writes go through `execute_command(...)`.
  - Reads go through `execute_query(...)`.
  - Dispatching the wrong kind should continue to fail with `TypeError`.
- Command and query inputs are frozen, slotted dataclasses.
- `tenant_id` is always the first field on every command and query.
- Caller-owned transaction support is part of the design and must remain intact.
- Public failures must stay within the documented `BpmnCoreError` hierarchy.
- User-scoped operations must validate tenant membership before acting.
- `CompleteTaskCommand.task_payload` persists as process metadata.
- Lane assignment is driven by BPMN lane metadata plus
  `properties_json["lane_owners"]` on the stored definition.

When changing the public contract, update all of:

- `src/m8flow_bpmn_core/api.py`
- `doc/api.md`
- relevant usage/example docs in `doc/`
- tests that lock the behavior

## Current Scope

Supported today:

- importing BPMN and optional DMN definitions
- starting process instances from stored definitions
- running SpiffWorkflow-backed process execution
- materializing human tasks and pending worklists
- claiming and completing human tasks
- persisting process metadata and event history
- lifecycle transitions such as suspend, resume, error, retry, terminate

Explicitly not covered yet:

- RBAC and workflow permission matrices
- external service task worker infrastructure
- timer events and scheduling
- tenant management APIs
- user management APIs
- BPMN messaging and correlation
- full operational dashboards and observability control plane

See `doc/gaps.md` before assuming a capability exists.

## Working Conventions

- Python version: 3.12
- Package layout: `src/`
- Tooling: `uv`, `pytest`, `ruff`, `alembic`, `hatchling`
- Keep changes small and aligned with the existing package boundaries.
- Prefer updating or adding tests with behavior changes.
- Prefer examples when validating end-to-end workflow behavior.

Common commands:

- `uv sync`
- `uv sync --extra postgresql`
- `make lint`
- `make test`
- `make test-integration`
- `make package`
- `uv run python examples/parallel_review_poc.py`
- `uv run python examples/errors_demo.py`

## Change Checklist

Before finishing a change, verify the following as applicable:

- behavior is covered by tests
- public API docs still match the code
- examples still reflect supported usage
- packaging still works if dependency or export surfaces changed
- new workflow semantics do not bypass tenant boundaries
