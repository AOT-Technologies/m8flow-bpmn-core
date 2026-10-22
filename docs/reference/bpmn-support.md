# BPMN support

This page lists the BPMN 2.0 and DMN elements that m8flow-core runs and
tests. The engine is [SpiffWorkflow](https://github.com/sartography/SpiffWorkflow),
so BPMN files use SpiffWorkflow's dialect, including its
`spiffworkflow` extension elements.

"Supported" means the element is covered by the tests or examples. Other
elements may parse and run through SpiffWorkflow, but they aren't tested and
the library doesn't guarantee them.

## Definitions

A definition must meet the following rules:

- A BPMN file must contain exactly one executable process, or you must pass
  `bpmn_process_id` to choose one when you start an instance.
- A definition can include one DMN file next to the BPMN, imported with
  `source_dmn_xml`.
- Collaborations with one participant and its lane set are supported.

## Supported elements

The following elements are supported:

| Element | Notes |
|---|---|
| Start event (none) | |
| End event (none) | |
| Sequence flow with condition expression | Python expressions evaluated against task data |
| User task | Becomes a human task that users claim and complete |
| Manual task | Becomes a human task, like a user task |
| Task (untyped) | Becomes a human task, like a user task |
| Script task | Python script run by the SpiffWorkflow script engine |
| Business rule task | Evaluates a DMN decision table from the definition's DMN file |
| Service task | Runs through a registered connector, synchronously. Uses the `spiffworkflow:serviceTaskOperator` extension. See [Call external services](../how-to/call-external-services.md). |
| Exclusive gateway | Split and merge |
| Parallel gateway | Split and join |
| Lane and lane set | Drive task assignment, together with `lane_owners` |
| Timer start event | `timeDate`, or a finite recurring `timeCycle` such as `R3/PT20S`. Each firing creates a new process instance. |
| Timer intermediate catch event | The instance waits until the scheduler fires the timer |
| Interrupting timer boundary event | `timeDate` or `timeDuration`, such as `PT2M`. Cancels the attached task and continues on the timeout path. |

Timers need a scheduler loop. See
[Run timers and scheduled retries](../how-to/run-timers-and-retries.md).

## Task assignment

A human task becomes a work item, assigned to the owners of its lane. Lane
owners come from the `lane_owners` mapping, from lane name to a list of
usernames. You can set it in `properties_json["lane_owners"]` when you import
the definition, or a script task can set it in task data at run time.

`lane_owners` is optional. A work item in a lane with no owners waits as
`READY` until your application adds potential owners with
`assign_pending_tasks_for_user`.

## Not supported yet

The following elements and behaviors aren't covered:

- Message events, signal events, and message correlation
- Call activities
- Event subprocesses
- Compensation
- Error and escalation boundary events
- Non-interrupting boundary events
- Multi-instance activities
- Asynchronous service tasks that complete through a callback

[Limitations](limitations.md#advanced-bpmn-coverage) describes each of them.

## Related pages

- [Limitations](limitations.md)
- [Run the examples](../how-to/run-the-examples.md): the examples cover most
  supported elements
- [About scheduling](../explanation/scheduling.md)
