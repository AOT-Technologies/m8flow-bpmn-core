# Service Task Architecture

This document captures the V1 execution seam for BPMN service tasks. The
library can now execute BPMN `ServiceTask` nodes synchronously when the host
application registers one or more connectors in the in-process registry.

## Direction

The current direction is to stay compatible with m8flow by treating the
existing connector-proxy as the only deployed connector runtime for now.

- `m8flow-bpmn-core` remains an in-process Python library.
- A host application registers one or more service-task connectors with the
  library.
- The initial real connector implementation will be an adapter that talks to
  `m8flow-connector-proxy`.
- Future connector runtimes such as NodeWire can be added later behind the
  same registry and connector protocol.

## Operation Id Convention

Spiff service tasks identify an operator as a string. For m8flow
compatibility, the library now treats that operator id as:

```text
<connector_key>/<command_name>
```

Examples from the current connector-proxy catalog:

- `http/GetRequestV2`
- `smtp/SendHTMLEmail`
- `postgres_v2/DoSQL`

The public helpers in `m8flow_bpmn_core.api` are:

- `build_service_task_operation_id(connector_key, command_name)`
- `split_service_task_operation_id(operation_id)`

## Connector-Proxy Contract

The current proxy exposes three important conventions:

- `GET /v1/commands`
  Returns the available command catalog. Each item identifies a connector
  command such as `http/GetRequestV2` together with its parameter metadata.
- `POST /v1/do/{connector_key}/{command_name}`
  Executes a command. For example, `POST /v1/do/http/GetRequestV2`.
- Reserved request keys prefixed with `spiff__`
  The proxy strips these before building the underlying connector command.

Important reserved keys:

- `spiff__task_data`
  Task/workflow data passed through to the connector command.
- `spiff__callback_url`
  Proxy callback target for async connectors. The library service-task layer
  will start with synchronous execution only, but this key is part of the
  stable contract because it already exists on the proxy side.

Proxy failures are surfaced as JSON error payloads. The library adapter should
translate those failures into `ServiceTaskExecutionError`.

## Current Runtime Behavior

Real process-instance execution paths now use the registry automatically:

- initial process-instance start
- timer-started process-instance start
- workflow advancement after a user task is completed
- waiting-workflow refresh after a timer wakes an instance

When Spiff reaches a BPMN service task, the library:

1. reads the service-task operator id such as `demo/PrepareReview` or
   `http/GetRequestV2`
2. evaluates the BPMN parameter expressions through the normal script engine
3. resolves the connector from the registry
4. executes the connector synchronously
5. stores the connector result in the BPMN result variable

Failures are surfaced to the caller as `ServiceTaskExecutionError`. The
runtime preserves a retryable workflow snapshot and moves the process
instance into `error` on the autonomous-persistence paths listed below.

Current autonomous failure-persistence coverage:

- initial process-instance start
- timer-started process-instance start
- waiting-workflow refresh after a timer wakes an instance
- retry reruns of an already-errored service-task branch

When a synchronous service task fails on one of those paths, the runtime:

1. keeps the Spiff workflow snapshot with the failed service task in `ERROR`
2. syncs that snapshot into the persisted runtime state
3. records a `task_failed` process-instance event for the service-task guid
4. transitions the process instance to `error`
5. re-raises `ServiceTaskExecutionError` back to the caller

That means the caller sees the failure immediately, but the same process
instance can later be retried either directly with
`RetryProcessInstanceCommand` or indirectly through
`ScheduleProcessInstanceRetryCommand`.

Workflow advancement after a user task is completed already executes service
tasks through the same registry, but its failure state still participates in
the caller-owned transaction boundary in V1. If the host application rolls
that outer transaction back, the task-completion write and the resulting
service-task failure state roll back together.

## Transaction Rule

The service-task design has to preserve two behaviors at the same time:

1. the caller sees synchronous connector failures immediately as
   `ServiceTaskExecutionError`
2. the same process instance remains retryable afterwards

That combination matters because many host applications wrap library calls in
an outer transaction helper such as `db.session_scope(...)`, which rolls back
the current transaction whenever an exception escapes.

The current library contract is therefore:

- successful workflow progression stays inside the caller-owned transaction
- synchronous service-task failures on the covered autonomous paths may persist
  a limited recovery state outside that caller-owned transaction
- that autonomous persistence commits only the workflow records needed for
  recovery:
  - the process instance row
  - the failed workflow snapshot
  - task and human-task state needed to represent the failure accurately
  - `task_failed` / `process_instance_error` lifecycle events
  - the final `error` status on the process instance
- unrelated caller-side writes must remain outside that autonomous safeguard
- post-user-task workflow advancement is not yet part of that autonomous
  safeguard

For initial process start specifically, this rule implies a two-phase flow:

1. persist a minimal baseline process instance and runtime shell
2. run the risky synchronous service-task path
3. if the connector fails, persist the errored retry snapshot durably and then
   re-raise `ServiceTaskExecutionError`

This transaction rule is what allows the library to keep caller-owned
transactions and still guarantee that retry commands have durable state to
work with after a synchronous connector failure.

Postgres is the reference database for this behavior. The repository keeps
dialect-specific SQLite test `xfail`s around the autonomous persistence
regressions because SQLite's locking model does not reliably reproduce an
independent writer while the outer failing transaction is still open.

On retry, the library restores the persisted workflow, resets the errored
service-task branch, reruns the workflow synchronously, and then persists the
new state. If the retried connector call succeeds, the process moves forward
normally. If it fails again, the process goes back to `error` and emits a new
`task_failed` / `process_instance_error` pair.

## Public Library Types

The public service-task seam in `m8flow_bpmn_core.api` includes:

- `ServiceTaskParameterDefinition`
  Stable description of one connector parameter.
- `ServiceTaskCommandDefinition`
  Stable description of one executable connector command.
- `ServiceTaskContext`
  Runtime context for a service-task execution, including tenant and process
  instance metadata.
- `ServiceTaskRequest`
  A concrete invocation request containing the operation id and parameters.
- `ServiceTaskResult`
  The connector result payload returned to the BPMN runtime.
- `ServiceTaskConnector`
  Protocol implemented by any connector adapter.
- `ServiceTaskRegistry`
  In-process registry that resolves operation ids to registered connectors.
- `ConnectorProxyServiceTaskConnector`
  Concrete adapter for one connector-proxy command group such as `http` or `smtp`.
- `fetch_connector_proxy_command_definitions(...)`
  Reads the live proxy catalog from `/v1/commands`.
- `build_connector_proxy_service_task_connectors(...)`
  Groups the live proxy catalog into connector instances.
- `build_connector_proxy_service_task_registry(...)`
  Convenience helper for the common case where the host app wants a complete
  registry from one proxy base URL.
- `service_task_registry_scope(...)`
  Context manager for request-scoped or test-scoped registry overrides.
- `set_default_service_task_registry_factory(...)`
  Process-wide default registry factory hook.

The typical m8flow-compatible host setup is now:

```python
from m8flow_bpmn_core import api

registry = api.build_connector_proxy_service_task_registry(
    "http://localhost:6844"
)

with api.service_task_registry_scope(registry):
    ...
```

The repository also now includes a runnable connector-proxy example at
`examples/service_task_connector_poc.py`. It uses the live m8flow
connector-proxy catalog, executes real BPMN `ServiceTask` nodes through
`http/GetRequestV2`, and keeps the resulting process instance visible in the
shared m8flow UI when the local shared database is in use.

For the rollback-safe failure contract specifically, see
`examples/service_task_failure_retry_poc.py`. That POC starts the first run
with a committed process-instance shell and an intentionally empty service-task
registry so the first service task fails during workflow initialization, lets
that failure escape a caller-owned `session_scope(...)`, and then shows that
the same process instance can still be retried and completed afterwards with a
working registry installed.

## What Is Still Missing

The execution seam is in place, but the broader connector story is not done yet:

- no delayed backoff, dead-letter queue, or operator inbox for repeated
  service-task failures yet
- no async callback/correlation flow for long-running connector executions yet
- post-user-task service-task failure persistence still shares the caller
  transaction boundary
