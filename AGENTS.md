# AGENT.md

## Purpose

`m8flow-core` is an in-process Python library built on top of
`SpiffWorkflow`. The goal is to provide a reusable BPMN/DMN workflow core for
M8Flow in the same general spirit as an embeddable Camunda-style library:
callers import Python APIs directly and run workflow logic inside their own
application process.

This repository is not an HTTP service. Do not design changes around REST
controllers, request handlers, or transport-layer DTOs unless the project
direction explicitly changes.

## Source Of Truth

Read these first before changing behavior:

- `docs/reference/api.md`: public API contract and error semantics
- `docs/reference/database-schema.md`: tables and columns that are part of
  the compatibility contract
- `docs/how-to/embed-in-your-application.md`: expected integration pattern for
  callers
- `docs/how-to/run-the-examples.md`: runnable examples
- `docs/reference/bpmn-support.md`: supported BPMN patterns
- `docs/how-to/build-and-install-the-package.md`: build and packaging workflow
- `docs/reference/limitations.md`: intentionally out-of-scope areas
- `docs/README.md`: index of every page, organized as tutorials, how-to
  guides, reference, and explanation

If code and docs disagree, treat that as a bug and bring them back into sync.

## Architecture

- `src/m8flow_core/api.py`
  - Stable public entrypoint for downstream consumers.
  - Re-exports commands, queries, errors, enums, and service functions.
- `src/m8flow_core/application/commands.py`
  - Write-side command dataclasses.
- `src/m8flow_core/application/queries.py`
  - Read-side query dataclasses.
- `src/m8flow_core/application/dispatcher.py`
  - `execute_command(...)` and `execute_query(...)`.
  - Accepts either a SQLAlchemy `Session` or `Connection`.
  - When given a `Connection`, it opens a temporary `Session` and does not
    commit or roll back. The caller owns the transaction boundary.
- `src/m8flow_core/services/`
  - Business logic by concern: process definitions, process instances, tasks,
    tenant-user checks, and SpiffWorkflow runtime integration.
- `src/m8flow_core/models/`
  - SQLAlchemy ORM models. Public API returns these models directly.
- `tests/fixtures/`
  - BPMN and DMN fixtures used by tests and examples.
- `examples/`
  - Runnable reference flows, including conditional approval, parallel review,
    and error demonstrations.

## Public API Invariants

Preserve these unless you are intentionally making a contract change:

- `m8flow_core.api` is the public surface.
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
  `properties_json["lane_owners"]` and `properties_json["initiator_lanes"]`
  on the stored definition.
- Every timestamp is a timezone-aware UTC `datetime`; no public parameter or
  column uses epoch seconds.
- Claim state lives in `work_item`; authorization targets are exact
  `(resource_type, resource_id)` pairs; JSON payloads are keyed by tenant and
  hash.
- Released Alembic migrations are immutable; the 1.0 baseline `k2l3m4n5o6p7`
  is the first.

When changing the public contract, update all of:

- `src/m8flow_core/api.py`
- `docs/reference/api.md`, and `docs/reference/database-schema.md` for schema
  changes
- relevant how-to guides and examples in `docs/`
- `CHANGELOG.md`, under `[Unreleased]`
- tests that lock the behavior, including `tests/test_api_surface.py` and
  `tests/test_compatibility_contract.py`

## Current Scope

Supported today:

- importing BPMN and optional DMN definitions
- starting process instances from stored definitions
- running SpiffWorkflow-backed process execution
- materializing human tasks and pending worklists
- claiming and completing human tasks
- persisting process metadata and event history
- lifecycle transitions such as suspend, resume, error, retry, terminate
- tenant-scoped command authorization (V1 roles) with pluggable policies
- timer events and scheduled retries through persisted scheduler jobs
- synchronous BPMN service tasks through pluggable connectors

Explicitly not covered yet:

- permission checks across the whole API (only eight commands are enforced)
- external service task worker infrastructure and async connectors
- multi-worker scheduler coordination
- tenant management APIs
- user management APIs
- BPMN messaging and correlation
- full operational dashboards and observability control plane

See `docs/reference/limitations.md` before assuming a capability exists.

## Working Conventions

- Python version: 3.12
- Package layout: `src/`
- Tooling: `uv`, `pytest`, `ruff`, `mypy`, `bandit`, `pre-commit`, `alembic`,
  `hatchling`
- Keep changes small and aligned with the existing package boundaries.
- Prefer updating or adding tests with behavior changes.
- Prefer examples when validating end-to-end workflow behavior.

Common commands:

- `uv sync`
- `uv sync --extra postgresql`
- `make lint`
- `make typecheck`
- `make security`
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
