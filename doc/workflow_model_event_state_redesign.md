# Workflow model and event-state redesign contract

This document records the final breaking contract for the workflow model and
event-state redesign.

## Compatibility goals

The coordinated M8Flow migration must continue to support:

- query claimable work using the `work_item` table and model;
- claim and complete work using work-item identifiers;
- read historical process and task events using the existing event type values;
- compare persisted task states such as `READY`, `COMPLETED`, `ERROR`, and
  `CANCELLED`;
- use the split lifecycle and task event enums.

Legacy structures are removed by the coordinated destructive migration.

## Target ownership of workflow fields

`work_item` becomes the canonical owner of human-task claim state:

| Field group | Canonical owner | Compatibility behavior |
| --- | --- | --- |
| tenant and process instance identity | `work_item` | Existing tenant and process identifiers remain valid. |
| task identity (`task_guid`, legacy task id) | `work_item` plus `task` | Existing task identifiers remain unchanged. |
| lane assignment | `work_item` | Existing lane/group assignment behavior remains unchanged. |
| actual owner and completing user | `work_item` | Canonical claim state. |
| claim/completion status | `work_item` | Existing `completed` and `task_status` values remain available. |
| claim/completion timestamps | `work_item` | Timezone-aware datetimes. |
| task and process display metadata | task/process definition models | Not copied into the new work-item table. |

The new work-item model must not duplicate metadata already available from the
task, task definition, process, or process definition models, including task
names, titles, types, lane names, process display names, BPMN identifiers,
form filenames, and JSON metadata.

## Migration strategy

The migration creates and backfills `work_item` from existing `human_task`
rows, migrates assignments, then removes the legacy tables. During the
coordinated deployment:

1. All queries return canonical work-item results.
2. Claim, completion, reopen, cancellation, termination, and error transitions
   update work-item state.
3. New work-item rows preserve tenant boundaries and task identity.

Any inconsistent legacy relationship, duplicate work-item identity, or missing
required tenant/process/task reference must stop the migration before partial
backfill.

## Event type contract

The split enums are the canonical internal vocabulary:

- `ProcessLifecycleEventType` for process-instance lifecycle events;
- `TaskEventType` for task events;
- `ProcessInstanceEventCategory` for persisted event classification.

Existing event string values must not change. The
`process_instance_event.category` column is additive and must be populated for
new events and backfilled for legacy events.

Historical rows are backfilled before the category is made non-null.

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

## Historical compatibility checklist (superseded)

The checklist below records the earlier additive-release contract. It is
retained as history only; the final release follows the breaking contract in
`doc/release_migration_strategy.md` and does not defer removal of legacy
models, aliases, or adapters.

The redesign is implemented as a coordinated breaking migration. Downstream
applications must complete the following before deployment:

1. apply the complete breaking migration chain;
2. query `WorkItemModel` and use work-item identifiers for claimable work;
3. use the split lifecycle and task event enums with non-null categories;
4. use `TaskState` for workflow execution state and `WorkItemState` for claim
   state; and
5. verify the application’s upgrade and rollback backups before production
   deployment. Downgrading removes the additive `work_item` table and event
   category column, so normalized-only data must not be written before the
   legacy consumer path is retained.

## Acceptance checks

The implementation must prove that:

- all human-task queries use `work_item` or have a documented migration path;
- claim and completion behavior is unchanged;
- event history remains readable before and after migration;
- all runtime task-state decisions use `TaskState` rather than bare literals;
- examples and integration tests continue to pass;
- tenant and task identities are preserved during backfill.
