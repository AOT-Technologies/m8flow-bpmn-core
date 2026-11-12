<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Run compensation handlers

Use compensation to undo activities that completed successfully. Attach a
compensation boundary event to the forward activity and associate it with an
activity marked `isForCompensation="true"`. The handler is outside normal
sequence flow:

```xml
<boundaryEvent id="payment_compensation" attachedToRef="charge_payment"
               cancelActivity="false">
  <compensateEventDefinition />
</boundaryEvent>
<userTask id="refund_payment" name="Refund payment" isForCompensation="true" />
<association id="payment_undo" sourceRef="payment_compensation"
             targetRef="refund_payment" />
```

Trigger compensation from an intermediate throw or end event:

```xml
<intermediateThrowEvent id="undo_order">
  <compensateEventDefinition waitForCompletion="true" />
</intermediateThrowEvent>
```

Without `activityRef`, the throw selects completed, uncompensated activities
in its runtime scope and invokes their handlers sequentially in reverse
completion order. Set `activityRef="charge_payment"` to select just that
activity. Activities that did not complete are ineligible. An empty selection
is a successful no-op. Another throw does not rerun consumed handlers.

The default `waitForCompletion="true"` holds the throwing token until its
handlers finish. With `false`, normal flow continues while compensation can
wait for a human task or timer. The instance remains active until both normal
flow and compensation finish. Your application uses its existing task APIs
and scheduler loop; no background worker is created by a throw.

## Complete human handlers

Human handlers appear in `GetPendingTasksQuery`. Claim and complete them using
`ClaimTaskCommand` and `CompleteTaskCommand`. The usual tenant, authorization,
lane-owner, and initiator-lane rules apply. Suspension prevents completion
until the instance resumes. Termination cancels outstanding compensation.

Supported handlers are user, manual, untyped, script, business rule, service
tasks, and embedded subprocesses containing supported elements. Completed
subprocesses retain their compensation scopes. Without an explicit handler,
default compensation recursively invokes their eligible child handlers.
An explicit subprocess handler replaces that recursion. A throw in a parent
cannot compensate children of a subprocess that is still active.

Boundary handlers receive the applicable scope data. Completed subprocess
scopes retain their completion data. Keep distinct business receipts under
distinct variable names, such as `inventory_receipt` and `payment_receipt`.
Handler-local output does not overwrite unrelated forward branches.

## Inspect progress and retry a failure

```python
records = api.execute_query(
    session,
    api.GetProcessInstanceCompensationsQuery(tenant_id, instance_id),
)
for record in records:
    print(record.activity_id, record.status, record.attempts)
```

Records are ordered by ascending completion sequence. Existing history queries
include `compensation_started`, `compensation_completed`, and
`compensation_failed` events, linked to the handler task GUID.

A handler failure stops advancement and puts the instance in `error`.
Connector failures raise `ServiceTaskExecutionError`; other handler failures
raise `CompensationExecutionError`. Catch the failure and commit the session
if you want to retain that recovery point:

```python
try:
    api.execute_command(session, complete_handler_command)
except (api.ServiceTaskExecutionError, api.CompensationExecutionError):
    session.commit()  # Saves the failed handler and preceding undo completions.
    raise
```

Retry with `RetryProcessInstanceCommand` or
`ScheduleProcessInstanceRetryCommand`. Retry resumes the failed handler and
then continues the remaining order; completed handlers stay completed.
Operator metadata updates are applied to the failed task on retry.

Compensation snapshots, journal rows, work items, and events share your
transaction. The library does not independently commit compensation progress,
including failures on start, timer wake-up, or retry. Rolling back also rolls
back undo completion records. A restart skips handlers whose completion was
committed. An external operation interrupted before commit can execute again.
Connectors receive `request.context.metadata["compensation"]` with the source
task GUID, scope ID, run ID, and stable idempotency key; use the key to
deduplicate uncertain external calls.

## Run the example and upgrade

Run `uv run python examples/compensation.py`. The SQLite example reserves
inventory, charges payment, arranges shipment, and compensates in shipment →
payment → inventory order. Press Enter to complete each pending handler and to
recreate the engine after committing shipment undo. The walkthrough shows the
restored journal and verifies that all three handlers have one attempt. Add
`--non-interactive` to run it without prompts.

Apply Alembic head before using an existing database. The additive revision
`m8f_671_compensation` follows signal revision `l3m4n5o6p7q8`. Existing
workflows without compensation continue to restore normally. Legacy compensation
instances lack a reliable completion journal. Import the definition and start
a new instance to run compensation.

Compensation event subprocesses, loop/multi-instance characteristics, call
activities, transaction cancellation, and automatic compensation from BPMN
errors are outside this release. Compensation models requiring these features
are rejected at import.
