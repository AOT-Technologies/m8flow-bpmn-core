# Breaking schema and API contract

This document defines the target contract for the coordinated breaking release
of `m8flow-bpmn-core` and M8Flow. It is the implementation target for the
remaining migration work; the current compatibility implementation must not be
treated as the final schema.

## Canonical persistence model

The final schema uses the following names and relationships:

| Current structure | Canonical structure | Final action |
| --- | --- | --- |
| `group` | `m8f_group` | Rename the table and rebuild foreign keys/indexes. |
| `human_task` claim state | `work_item` | Move claim, owner, status, lane, and timestamps to `work_item`; remove the legacy table after validation. |
| `human_task_user` | work-item assignment rows | Migrate assignments and remove the legacy table. |
| `tenant_specific_field_1` | `realm_identifier` | Rename the physical column. |
| `tenant_specific_field_2` | `external_org_id` | Rename the physical column. |
| `tenant_specific_field_3` | `external_user_id` | Rename the physical column. |
| `spiff_serializer_version` | `workflow_engine_version` | Rename the physical column and update persistence callers. |
| `single_process_hash` / `full_process_model_hash` | canonical process digest | Validate collisions, backfill one digest, then remove the legacy columns. |
| URI permission targets | `(resource_type, resource_id)` | Convert all resolvable records and remove URI matching/fallback. |
| `ProcessInstanceEventType` | lifecycle/task event enums | Make split enums canonical and remove the combined enum. |
| nullable event `category` | non-null `category` | Backfill and enforce `NOT NULL`. |
| `*_in_seconds` columns | timezone-aware `DateTime` | Remove legacy columns and ORM mappings. |

Historical Alembic revisions remain immutable. Legacy names may therefore still
appear inside old migration files, but they must not remain in the final ORM,
service code, public API, fixtures, or new migrations except where a migration
must explicitly identify an old column to rename or remove it.

## Public API target

The final public API must:

- expose `ProcessLifecycleEventType` and `TaskEventType`, but not
  `ProcessInstanceEventType`;
- expose work-item commands and queries using `work_item_id` where applicable;
- accept timezone-aware `datetime` values only for persisted timestamps;
- expose named tenant identity fields;
- accept explicit authorization resource pairs only;
- avoid public parameters and return attributes named `*_in_seconds`;
- avoid public process-definition parameters named
  `single_process_hash` or `full_process_model_hash`;
- use `workflow_engine_version` instead of `spiff_serializer_version`.

## Migration invariants

The destructive migration must stop before changing schema when any of these
conditions is detected:

- a human task cannot be represented by exactly one work item;
- a human-task assignment cannot be mapped to a work-item assignment;
- identity aliases contain conflicting values;
- process digests would collide after consolidation;
- a URI permission cannot be mapped to a resource pair;
- an event type cannot be assigned a lifecycle or task category;
- a legacy timestamp cannot be converted losslessly;
- a tenant-scoped JSON reference is ambiguous, orphaned, or cross-tenant.

The final release is a coordinated deployment. No older M8Flow API process,
worker, scheduler, or migration may run after the destructive migration has
been applied.
