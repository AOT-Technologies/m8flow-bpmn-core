# Call external services from service tasks

This guide shows how to run BPMN service tasks: how to name the operation in
BPMN, how to connect it to the m8flow connector proxy or to your own Python
connector, and how to install the connectors for a workflow call. The library
runs service tasks synchronously, inside the same call that advances the
workflow.

## Name the operation in BPMN

A service task names its operation with a `spiffworkflow:serviceTaskOperator`
extension. The operation ID has the form `<connector_key>/<command_name>`,
such as `http/GetRequestV2` or `smtp/SendHTMLEmail`. The following task calls
the `PrepareReview` command of a `demo` connector and stores the result in the
`service_stage_one` variable:

```xml
<bpmn:serviceTask id="Task_prepare" name="Prepare Review">
  <bpmn:extensionElements>
    <spiffworkflow:serviceTaskOperator
      id="demo/PrepareReview"
      resultVariable="service_stage_one">
      <spiffworkflow:parameters>
        <spiffworkflow:parameter
          id="submission_message"
          type="str"
          value="submission_message" />
      </spiffworkflow:parameters>
    </spiffworkflow:serviceTaskOperator>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

Each parameter `value` is an expression that the library evaluates against
the task data before it calls the connector. The `spiffworkflow` prefix maps
to `http://spiffworkflow.org/bpmn/schema/1.0/core`.

## Use the m8flow connector proxy

If you run [`m8flow-connector-proxy`](https://github.com/AOT-Technologies/m8flow),
build a registry from its live command catalog and install it around your
workflow calls:

```python
from m8flow_core import api

registry = api.build_connector_proxy_service_task_registry(
    "http://localhost:6844"
)

with api.service_task_registry_scope(registry):
    api.execute_command(connection, api.InitializeProcessInstanceFromDefinitionCommand(...))
```

The helper reads `GET /v1/commands` from the proxy and creates one connector
for each connector key in the catalog. Pass `timeout_seconds` to change the
request timeout. Replace `http://localhost:6844` with your proxy's base URL;
that's the default port in a local m8flow stack.

## Write your own connector

A connector is any object with a `connector_key`, a `list_commands()` method,
and an `execute(request)` method. The following connector handles the
`demo/PrepareReview` operation from the preceding BPMN:

```python
from m8flow_core import api


class DemoConnector:
    connector_key = "demo"

    def list_commands(self):
        return (
            api.ServiceTaskCommandDefinition(
                connector_key=self.connector_key,
                command_name="PrepareReview",
            ),
        )

    def execute(self, request: api.ServiceTaskRequest) -> api.ServiceTaskResult:
        message = (request.parameters or {}).get("submission_message")
        return api.ServiceTaskResult(payload={"prepared": True, "message": message})


registry = api.ServiceTaskRegistry()
registry.register_connector(DemoConnector())
```

The request carries the evaluated `parameters`, the `task_data`, and a
`context` with the tenant ID, process instance ID, and task details. The
`payload` you return must be JSON-serializable. The library stores it in the
BPMN result variable.

To report a failure, raise an exception. The library surfaces it to your
caller as `ServiceTaskExecutionError`.

## Install connectors for the whole process

`service_task_registry_scope` installs a registry for the duration of a `with`
block, which suits a request or a test. To install one for every workflow call
in the process, register a factory once at startup:

```python
api.set_default_service_task_registry_factory(lambda: registry)
```

Install the same registry around your scheduler loop too. A timer that fires
can advance a workflow into a service task, as
`sample_app/docs/scheduler.md` describes.

## Recover from a failed service task

When a service task fails while a process starts, while a timer wakes it, or
during a retry, the library does the following:

1. It raises `ServiceTaskExecutionError` to your caller.
2. It still saves the instance in `error`, with the failed task and a
   `task_failed` event, even if your code then rolls back its transaction.

To run the failed service task again, retry the instance with
`RetryProcessInstanceCommand`, or schedule a retry as described in
[Run timers and scheduled retries](run-timers-and-retries.md#schedule-a-retry-for-a-failed-process-instance).

A service task that runs after `CompleteTaskCommand` behaves differently: its
failure state is part of your transaction, so rolling back also undoes the
task completion. [About service task execution](../explanation/service-task-execution.md#the-failure-transaction-rule)
explains why.

## Related pages

- [About service task execution](../explanation/service-task-execution.md)
- [Service task hooks in the API reference](../reference/api.md#service-task-hooks)
- [Run the examples](run-the-examples.md#service-task-examples): the
  connector proxy and failure-and-retry examples
