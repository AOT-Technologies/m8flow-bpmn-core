<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Publish and deliver BPMN signals

Signals publish an occurrence to every eligible subscription in the tenant.
They do not select one process instance or use correlation keys. A throw with
zero listeners succeeds. See BPMN 2.0.2 sections 10.5.1 and 10.5.6 for the
[publication and event-handling rules](https://www.omg.org/spec/BPMN/2.0.2/PDF).

## Model a signal

Declare a signal with a name and reference it from the event definition:

```xml
<signal id="notice" name="Notice" />
<intermediateThrowEvent id="publish_notice">
  <signalEventDefinition signalRef="notice" />
</intermediateThrowEvent>
```

Match the resolved name exactly, including case, in other definitions. XML
IDs are local to the BPMN document. The supported forms are intermediate and
end throws, intermediate catches, interrupting and non-interrupting boundaries,
top-level Signal Starts, and interrupting/non-interrupting event-subprocess
starts. Non-interrupting handlers can react to repeated occurrences while their
activity or scope is active. Human tasks created by signal handlers become work
items with the usual lane assignment.

Spiff signal `payloadExpression` and `variableName` extensions retain their
existing behavior. An external publication may carry any JSON value. A BPMN
throw without a payload expression publishes JSON null, rather than all process
variables. Signal payloads do not become process metadata automatically.

## Publish from Python

Use the public command with a stable occurrence ID supplied by your application:

```python
from m8flow_core import api

publication = api.execute_command(
    session,
    api.PublishSignalCommand(
        tenant_id=tenant_id,
        signal_name="Notice",
        user_id=publisher_id,
        occurrence_id="order-123-notice-1",
        payload={"order_id": 123},
    ),
)
```

The user must belong to the tenant and have `signal.publish` with `execute`
permission on that tenant. The built-in manager and admin roles grant it.
BPMN throws execute as workflow behavior and do not require an additional user
permission check.

Retry an external publication with the same tenant, occurrence ID, publisher,
name, and payload. It returns the original occurrence without adding recipients.
Changing those inputs while reusing the ID raises `ValidationError`. Occurrence
IDs are independent between tenants. Omit `published_at` to use current UTC;
supplied timestamps must be timezone-aware and are normalized to UTC.

Publication snapshots subscriptions that are eligible at that point. It does
not replay to catchers that start waiting later. One-shot catches are reserved
for their first captured occurrence. Repeatable handlers receive each distinct
occurrence. Repeating a delivery never repeats its committed consumption.

## Run durable deliveries

BPMN throws deliver to local catchers immediately and record those receipts.
Other instances and top-level Signal Starts wait for your host's delivery loop:

```python
with session_factory.begin() as session:
    attempted = api.execute_command(
        session,
        api.RunSignalDeliveriesCommand(tenant_id=tenant_id, limit=100),
    )
```

The runner advances workflows for the supplied tenant and has no acting-user
authorization check. Restrict access to trusted host code and derive or
validate the allowed `tenant_id` before invoking it.

Run one delivery loop per tenant. Overlapping calls for a tenant serialize
through the caller's transaction so they cannot consume the same receipt twice.
Each call selects a bounded batch of due recipients.
All subscriptions for one occurrence in the same instance are injected together;
that atomic recipient group can contain several returned receipts. Signals
produced by delivery are available to later runner calls.

Both dispatchers and the four signal service functions accept a SQLAlchemy
`Session` or caller-owned `Connection`. The library never commits or rolls back
your outer transaction. Commit publications to make them durable; commit runner
changes to acknowledge consumption. Rollback restores both workflow state and
receipts, including newly created instances.

Suspension retains subscriptions and holds captured deliveries until resume.
Completed, errored, and terminated instances stop accepting new publications.
A delivery whose catcher or scope ended before execution is recorded as
`skipped`. A failure before consumption leaves the delivery `pending`, with its
next attempt 30 seconds later. A downstream workflow failure records consumption
as `delivered`, saves the recipient's error state, and uses the existing workflow
recovery APIs. Other recipients continue; the publisher does not fail.

Signal-bearing workflows keep service-failure recovery in this same caller-owned
transaction, rather than using an independent failure-state write. Commit the
error state to retain it. This keeps workflow state and signal publication atomic.

Exactly-once consumption covers committed database workflow changes and process
creation. A connector that performs an external side effect must handle retries
according to its existing contract.

## Inspect outcomes

Use `GetSignalPublicationQuery(tenant_id, occurrence_id)` and
`ListSignalDeliveriesQuery(tenant_id, occurrence_id)` through `execute_query`.
The matching functions are `get_signal_publication` and `list_signal_deliveries`.
Receipts expose `status` (`pending`, `delivered`, or `skipped`), `attempts`,
`retry_at`, `delivered_at`, `last_error`, and `process_instance_id`.
Count receipts by status to inspect delivery outcomes separately from publication.

## Upgrade an existing database

Run `alembic upgrade head` before using the new package. The additive signal
migration follows the unchanged 1.0 baseline. A tenant's registry is initialized
on its first definition import or signal operation by reading stored definitions and restoring active
workflow states without executing them. For pre-existing definitions, the highest
definition ID per process model is initially active for Signal Starts. Later
imports select the most recently imported version, including when re-importing
an older version. Previously queued start deliveries retain their captured version.

Signal Starts use a dedicated tenant system user as initiator. Put their human
tasks in lanes if they should be assigned to people. Re-run `ensure_v1_role` for
existing manager/admin roles to provision the new publication grant.

Run `uv run python examples/signal_broadcast.py` to step through the SQLite
example. Add `--non-interactive` for scripted runs.
