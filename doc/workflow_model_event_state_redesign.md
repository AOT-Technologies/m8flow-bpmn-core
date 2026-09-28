# Workflow model and event-state redesign contract

This document records the compatibility contract and implementation status for
the workflow model and event-state redesign.

## Compatibility goals

Existing `m8flow` consumers must continue to be able to:

- query human tasks using the current `human_task` table and model;
- claim and complete tasks using the existing commands and identifiers;
- read historical process and task events using the existing event type values;
- compare persisted task states such as `READY`, `COMPLETED`, `ERROR`, and
  `CANCELLED`;
- use the existing `ProcessInstanceEventType` enum during migration.

The redesign may add models, columns, enums, and API exports. Removing or
renaming existing compatibility-facing fields requires a separately planned
consumer migration.

## Target ownership of workflow fields

`work_item` becomes the canonical owner of human-task claim state:

| Field group | Canonical owner | Compatibility behavior |
| --- | --- | --- |
| tenant and process instance identity | `work_item` | Existing tenant and process identifiers remain valid. |
| task identity (`task_guid`, legacy task id) | `work_item` plus `task` | Existing task identifiers remain unchanged. |
| lane assignment | `work_item` | Existing lane/group assignment behavior remains unchanged. |
| actual owner and completing user | `work_item` | Existing human-task attributes remain readable during migration. |
| claim/completion status | `work_item` | Existing `completed` and `task_status` values remain available. |
| claim/completion timestamps | `work_item` | Legacy epoch attributes remain synchronized while supported. |
| task and process display metadata | `human_task`/definition models | Not copied into the new work-item table. |

The new work-item model must not duplicate metadata already available from the
task, task definition, process, or process definition models, including task
names, titles, types, lane names, process display names, BPMN identifiers,
form filenames, and JSON metadata.

## Transitional persistence strategy

Part 2 creates and backfills `work_item` from existing `human_task` rows. Part
3 keeps the existing task API as the compatibility write surface while
synchronizing the normalized row for runtime-created and migrated tasks.
During the compatibility period:

1. Existing queries continue to return their current model and attributes.
2. The work-item row is the future canonical claim-state target.
3. Claim, completion, reopen, cancellation, termination, and error transitions
   synchronize both rows until downstream consumers migrate.
4. No existing human-task row is deleted as part of the initial split.
5. New work-item rows must preserve tenant boundaries and existing task IDs.

Any inconsistent legacy relationship, duplicate work-item identity, or missing
required tenant/process/task reference must stop the migration before partial
backfill.

## Event type contract

The split enums are the canonical internal vocabulary:

- `ProcessLifecycleEventType` for process-instance lifecycle events;
- `TaskEventType` for task events;
- `ProcessInstanceEventCategory` for persisted event classification.

`ProcessInstanceEventType` remains a compatibility enum containing the existing
combined values. Existing event string values must not change. The
`process_instance_event.category` column is additive and must be populated for
new events and backfilled for legacy events.

Legacy rows with a null category must remain queryable. Readers may derive the
category from the event type as a compatibility fallback until all rows are
backfilled.

## Task-state contract

Runtime code must use SpiffWorkflow's `TaskState` enum for comparisons and
assignments. Database persistence remains string-compatible during this
redesign:

- `TaskState.READY` persists as `READY`;
- `TaskState.COMPLETED` persists as `COMPLETED`;
- `TaskState.ERROR` persists as `ERROR`;
- `TaskState.CANCELLED` persists as `CANCELLED`.

`TaskModel.state` normalizes enum values to their persisted names and rejects
unknown execution states. The legacy `TERMINATED` value remains accepted only
for process-level compatibility; it is not presented as a SpiffWorkflow state.

The M8Flow-specific claim states (`CLAIMED`, `TERMINATED`, and similar
work-item states) remain separate from SpiffWorkflow execution states.

## Final implementation and consumer checklist

The redesign is implemented as an additive migration. Before removing any
legacy consumer reads, downstream applications should:

1. apply migrations through `e1f2a3b4c5d6`;
2. continue querying `HumanTaskModel` while adopting `work_item` for claim
   state when convenient;
3. accept either the combined event enum or the split event enums;
4. prefer `TaskState` values for execution-state comparisons and keep
   `WorkItemState` for human-task claim state; and
5. verify the application’s upgrade and rollback backups before production
   deployment. Downgrading removes the additive `work_item` table and event
   category column, so normalized-only data must not be written before the
   legacy consumer path is retained.

## Acceptance checks

Later implementation parts must prove that:

- old human-task queries still work or have a documented migration path;
- claim and completion behavior is unchanged;
- event history remains readable before and after migration;
- all runtime task-state decisions use `TaskState` rather than bare literals;
- examples and integration tests continue to pass;
- tenant and task identities are preserved during backfill.
