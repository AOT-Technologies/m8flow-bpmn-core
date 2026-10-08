# Breaking release and migration strategy

This document defines the coordinated rollout for the breaking schema release
that follows the compatibility foundation in core `0.2.0`. The core library
and M8Flow must be upgraded as one deployment unit because the final release
removes legacy columns, aliases, and compatibility-facing models.

The downstream implementation handoff is recorded in
[`m8flow_breaking_migration_prompt.md`](m8flow_breaking_migration_prompt.md).

## Supported rollout pairs

| Core package | M8Flow migration state | Support level |
| --- | --- | --- |
| `0.2.0` | Before `c3d4e5f6a7b8` | Compatibility mode; do not remove core epoch columns. |
| `0.2.0` | Through `c3d4e5f6a7b8` | Datetime columns backfilled; writers must use native datetime fields. |
| `0.2.0` | Through `d4e5f6a7b8c9` | Breaking cleanup applied; legacy core epoch columns are removed. |

The M8Flow migration `d4e5f6a7b8c9_remove_core_epoch_timestamp_columns.py`
is the downstream deployment equivalent of core migration
`f7a8b9c0d1e2_remove_legacy_epoch_columns.py`. The exact M8Flow application
release should record these revision pairs in its release notes. It must run
after the complete core breaking head `k2l3m4n5o6p7`, which also removes the
legacy group source flag and finalizes explicit authorization targets.

The downstream chain is a required release artifact, not an optional
follow-up. M8Flow must add and test its final migration against a database
upgraded through `k2l3m4n5o6p7`; the core repository cannot apply that
separate application migration on M8Flow's behalf.

## Required migration order

1. Deploy compatibility code that can read both old and new representations.
2. Stop workflow writers, schedulers, and background workers.
3. Take and verify a database backup.
4. Backfill datetime fields, tenant-scoped JSON rows, `work_item` rows, event
   categories, and explicit authorization resource fields.
5. Run conflict/orphan validation and stop on any ambiguous data.
6. Deploy M8Flow changes for datetime fields, work items, event enums, named
   identities, and resource-pair authorization.
7. Verify representative workflows, task claims, event history, JSON access,
   and scheduler rows for every tenant.
8. Apply the M8Flow destructive cleanup migration only after all consumers are on the
   new APIs.
9. Do not start older workers after the destructive migration; compatibility
   aliases and adapters are removed as part of this coordinated release.

Do not run steps 6–8 concurrently with an older worker process. A mixed
deployment can write legacy fields after they have been removed.

## Migration-specific consumer work

### Timestamps

Change ORM queries, serializers, and command inputs from epoch columns or
`*_in_seconds` parameters to timezone-aware UTC `datetime` fields. Internal
timer payload values such as `run_at_in_seconds` are transitional workflow
serialization data, not database timestamp columns.

### Authorization targets

Create `(resource_type, resource_id)` targets for every permission and migrate
known URI records using a reviewed mapping before deploying the breaking
release. A resource pair must be complete; partial pairs and URI records that
cannot be mapped unambiguously must abort the migration. The UI must send
explicit resource pairs before URI matching and fallback are removed.

### User and group identities

Map the legacy tenant identity fields to the named identity concepts
`realm_identifier`, `external_org_id`, and `external_user_id`. Compare values
before renaming physical columns, preserve user/group primary keys, and stop
when the old fields disagree. All tenant membership and lane-owner lookups
must use the named fields before the destructive migration.

### Work items

Backfill one `work_item` row for every legacy human task, preserving its ID,
tenant, process, task, lane, owner, completion, and status values. Verify that
claim, completion, reopen, termination, and reassignment behavior matches
before removing the legacy human-task tables.

### Events

Backfill `category` from the existing event type values. Migrate readers to
`ProcessLifecycleEventType` and `TaskEventType`, verify event counts and
ordering per process, then enforce non-null categories and remove the legacy
combined enum adapter in the same release.

## Rollback and backup requirements

The preferred rollback is restoration of the tested pre-migration backup. The
destructive migrations cannot reconstruct deleted epoch values or collapse
tenant-specific JSON rows without data loss. Downgrade scripts restore schema
shape only and may create empty compatibility columns.

Before production rollout, test both a logical/physical PostgreSQL restore and
the application startup path at the restored revision. Keep workflow writers
stopped until the restored database and migration revision have been verified.

See [`migration_runbook.md`](migration_runbook.md) for operational commands,
validation queries, and downgrade limitations.
