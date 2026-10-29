# Sample app architecture

This page describes how the sample app is put together: its components, its
built-in workflows, and its two operating modes. To run it, see the
[sample app README](../README.md).

## Components

The app is a Flask application with the following parts, under
`sample_app/`:

| Path | Role |
|---|---|
| `src/m8flow_sample_app/app.py` | Flask app factory. Runs migrations, seeds data, and starts the scheduler on startup. |
| `src/m8flow_sample_app/db.py` | SQLAlchemy engine and session helpers, and the startup Alembic runner |
| `src/m8flow_sample_app/settings.py` | Settings, read from `M8FLOW_SAMPLE_APP_*` variables |
| `src/m8flow_sample_app/models.py` | The app's own tables, such as `secret`, and the combined metadata for Alembic |
| `src/m8flow_sample_app/seed.py` | Sample tenants, users, role grants, lane owners, and default secrets |
| `src/m8flow_sample_app/auth.py` | Tenant and user session selection |
| `src/m8flow_sample_app/keycloak_login.py` | Keycloak redirect and callback sign-in for shared mode |
| `src/m8flow_sample_app/shared_m8flow.py` | Shared mode detection, Keycloak provisioning, and catalog publishing |
| `src/m8flow_sample_app/secrets.py` | Tenant-scoped secret storage |
| `src/m8flow_sample_app/service_tasks.py` | The service task registry, which resolves `M8FLOW_SECRET:<NAME>` parameters before calling the connector proxy |
| `src/m8flow_sample_app/scheduler.py` | The in-process scheduler loop. See [Scheduler](scheduler.md). |
| `src/m8flow_sample_app/workflows/` | Thin wrappers around the library's public API |
| `src/m8flow_sample_app/views/` | Pages for definitions, workflow start, tasks, process instances, and secrets |
| `src/m8flow_sample_app/web.py`, `ui.py` | Route registration and the shared page layout |
| `fixtures/` | The built-in BPMN workflows |
| `alembic/` | The app's migrations, including the library's tables |
| `scripts/` | Wheel staging and run scripts |

The `workflows/` wrappers use only the public API:

| Wrapper | Library calls |
|---|---|
| `deploy.py` | `ImportBpmnProcessDefinitionCommand`, with tenant-specific lane owners |
| `start.py` | `InitializeProcessInstanceFromDefinitionCommand` |
| `tasks.py` | `GetPendingTasksQuery`, `ClaimTaskCommand`, and `CompleteTaskCommand` |
| `instances.py` | `GetProcessInstanceQuery`, `GetProcessInstanceEventsQuery`, `GetProcessInstanceMetadataQuery`, and `ListProcessInstancesQuery` |

## Built-in workflows

### Reimbursement demo

Defined in `fixtures/sample_app_demo.bpmn`:

- Starts with **Submit Reimbursement Request**.
- Routes amounts over 1000 through **Finance Review**.
- Skips the final **Review Request** when Finance rejects.
- Sends the outcome by email through an SMTP service task on the connector
  proxy.

### Timeout escalation demo

Defined in `fixtures/sample_app_review_timeout_escalation.bpmn`:

- Starts with **Review Submitted Request** in the `Operations` lane.
- Attaches an interrupting boundary timer with a duration of `PT2M`.
- If the review is still open after two minutes, cancels it and creates
  **Supervisor Review** in the `Supervisor` lane.

## Operating modes

The app runs in one of two modes:

- **Standalone mode** uses a local tenant and user picker, for isolated runs.
- **Shared audit mode** runs against a local m8flow stack's database. It
  creates the sample users in the shared Keycloak realm, aligns tenant IDs
  with Keycloak organization IDs, and publishes deployed BPMN and DMN files to
  the m8flow backend's catalog, so the workflows appear in the m8flow UI.

[About the sample app integration](../../docs/explanation/sample-app-integration.md#standalone-and-shared-modes)
explains what shared mode changes and why.

## Related pages

- [Sample app README](../README.md)
- [Scheduler](scheduler.md)
