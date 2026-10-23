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
| Lane and lane set | Drive task assignment, together with `lane_owners` and `initiator_lanes` |
| Timer start event | `timeDate`, or a finite recurring `timeCycle` such as `R3/PT20S`. Each firing creates a new process instance. |
| Timer intermediate catch event | The instance waits until the scheduler fires the timer |
| Interrupting timer boundary event | `timeDate` or `timeDuration`, such as `PT2M`. Cancels the attached task and continues on the timeout path. |

Timers need a scheduler loop. See
[Run timers and scheduled retries](../how-to/run-timers-and-retries.md).

## Task assignment

A human task becomes a work item. Its potential owners, the users who can
claim it, depend on the task's lane. Lane names are compared after trimming
outer whitespace, ignoring case. A lane element with no `name`, or a name that
is empty or only whitespace, counts as no lane.

1. **No lane.** The user who started the instance is the only potential
   owner, labelled `process_initiator`.
2. **Initiator lane.** A lane listed in
   `properties_json["initiator_lanes"]` behaves like no lane: the initiator
   only, labelled `process_initiator`. `lane_owners` and the lane group are not
   consulted. A lane is never an initiator lane because of its name.
3. **Named lane owners.** For any other lane, the users listed for it in
   `lane_owners` are potential owners, labelled `lane_owner`. The mapping
   comes from `properties_json["lane_owners"]`, unless the task's data has a
   `lane_owners` mapping (for example, set by an earlier script task), which
   replaces it. Identifiers that match no user in the tenant are skipped with a
   logged warning.
4. **Owners join the lane group.** Each tenant has one group per lane. Named
   lane owners are added to it, at import and when a task runs.
5. **Lane group members.** Every other tenant member of the lane group is a
   potential owner, labelled `lane_assignment`.
6. **One row per user.** A user appears once per work item. A named owner who
   is also a group member is labelled `lane_owner`.
7. **No owners.** A work item with no potential owners waits as `READY` until
   your application adds some, for example with
   `assign_pending_tasks_for_user`.

When a task is materialized again, for example after a resume or retry, users
who now qualify are added. Existing potential owners are never removed.

Instances started by a timer start event have the tenant's scheduler system
user as initiator, so put their human tasks in a lane.

`initiator_lanes` is validated at import: it must be a list of non-empty lane
names that exist in the BPMN, and none of them may also be a key in
`lane_owners`.

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
