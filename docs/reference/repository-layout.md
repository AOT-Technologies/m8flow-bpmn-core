# Repository layout

This page describes what each folder in the repository holds.

## Top level

The repository root holds the following:

| Path | Contents |
|---|---|
| `src/m8flow_core/` | The library package |
| `tests/` | Unit tests, run with `make test` |
| `tests/fixtures/` | BPMN and DMN files used by the tests and the examples |
| `integration/` | The PostgreSQL integration test, run with `make test-integration` |
| `alembic/` | Migrations for the library's tables |
| `examples/` | Runnable example scripts and their launchers. See [Run the examples](../how-to/run-the-examples.md). |
| `sample_app/` | A Flask host application that uses the library as a wheel dependency. See the [sample app README](../../sample_app/README.md). |
| `docs/` | This documentation |
| `.github/` | CI, DCO sign-off, CodeQL, dependency review, secret-scan, and PyPI publish workflows; Dependabot settings; issue and pull request templates |
| `tools/check_dco.py` | The DCO sign-off check, run by the `commit-msg` hook and testable with `--rev-range` |
| `.pre-commit-config.yaml` | Local hooks: Ruff, mypy, and Bandit, plus the DCO `commit-msg` check |
| `.gitleaks.toml` | Secret-scan settings |
| `Makefile` | Development commands. Run `make help` to list them. |
| `pyproject.toml` | Package metadata, dependencies, extras, and tool settings |
| `sample.env` | Example environment variables |

## The library package

The package is split by responsibility:

| Path | Contents |
|---|---|
| `api.py` | The public entry point. Re-exports the commands, queries, errors, enums, and service functions documented in the [API reference](api.md). |
| `application/commands.py` | Command dataclasses, the write side |
| `application/queries.py` | Query dataclasses, the read side |
| `application/dispatcher.py` | `execute_command` and `execute_query`, which accept a `Session` or a `Connection` |
| `services/process_definitions.py` | Importing and validating BPMN and DMN definitions |
| `services/process_instances.py` | Process instance creation, lifecycle transitions, metadata, and events |
| `services/tasks.py` | Worklists, claim, and completion |
| `services/work_items.py` | Work item state and potential-owner assignment |
| `services/workflow_runtime.py` | The SpiffWorkflow integration: starting, restoring, advancing, and saving workflows |
| `services/tenant_users.py` | Tenant membership checks |
| `services/authorization.py` | Command authorization, roles, and policy hooks |
| `services/scheduler_jobs.py` | Storage helpers for the `scheduler_job` table |
| `services/scheduler_runtime.py` | `run_due_scheduler_jobs` and the timer and retry runtime |
| `services/service_tasks.py` | The service task connector protocol and registry |
| `services/connector_proxy_service_tasks.py` | The `m8flow-connector-proxy` adapter |
| `models/` | SQLAlchemy ORM models. The public API returns these directly. [Database schema](database-schema.md) lists them. |
| `models/task_types.py` | `USER_TASK_TYPENAMES`, the BPMN task types that become human work |
| `errors.py` | The `BpmnCoreError` hierarchy |
| `db.py` | Engine, session, and schema helpers |
| `settings.py` | Environment settings. See [Configuration](configuration.md). |
| `utils/keycloak/` | Keycloak helpers used by the examples and the sample app in shared m8flow mode |

The stable interfaces are `api.py` and the
[database schema](database-schema.md). Everything else can change between
releases.

## Related pages

- [About the architecture](../explanation/architecture.md)
- [Develop and test](../how-to/develop-and-test.md)
