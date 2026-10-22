# Upgrade to 1.0

This guide moves an application from `m8flow-bpmn-core` 0.x to m8flow-core
1.0. The release renames the package and finishes a breaking schema and API
redesign, so the upgrade changes your code, your dependencies, and your
database. Plan it as one coordinated deployment.

## Before you begin

You need the following:

- A tested backup of the database, and a way to restore it. Restoring the
  backup is the only lossless rollback.
- A maintenance window in which no workflow writer, scheduler, or worker runs.
- Every process that uses the library, including m8flow itself, ready to
  deploy at the new version together. No older process may run after the
  migration.

## Update the package name

The distribution and the import package are both renamed:

| 0.x | 1.0 |
|---|---|
| `pip install m8flow-bpmn-core` | `pip install m8flow-core` |
| `from m8flow_bpmn_core import api` | `from m8flow_core import api` |
| `M8FLOW_BPMN_CORE_CELERY_*` variables in the Celery example | `M8FLOW_CORE_CELERY_*` |

To update your code, follow these steps:

1. Replace the dependency `m8flow-bpmn-core` with `m8flow-core>=1.0,<2.0`.
2. Replace every `m8flow_bpmn_core` import with `m8flow_core`.
3. Rename any `M8FLOW_BPMN_CORE_` environment variables to `M8FLOW_CORE_`.

The database tables keep their names, so the rename alone needs no
migration.

## Update your code for the new API

The [API reference](../reference/api.md) has every field. The 1.0 API changes
the following:

| Area | 0.x | 1.0 |
|---|---|---|
| Timestamps | `*_in_seconds` epoch integers, such as `started_at_in_seconds` | Timezone-aware UTC `datetime` values, such as `started_at` |
| Human tasks | `HumanTaskModel` and `human_task_id` | `WorkItemModel` and `work_item_id` |
| Event types | `ProcessInstanceEventType` | `ProcessLifecycleEventType` and `TaskEventType`, with a non-null `category` |
| Event time | `timestamp` | `occurred_at` |
| Process digest | `single_process_hash` and `full_process_model_hash` | `process_xml_digest` |
| Scheduler | `run_due_scheduler_jobs(now_in_seconds=...)` | `run_due_scheduler_jobs(now=...)` |
| User identity | `tenant_specific_field_1`, `_2`, `_3` | `realm_identifier`, `external_org_id`, `external_user_id` |
| Authorization | URI targets such as `/tasks/%` | Exact `(resource_type, resource_id)` targets |
| Group table | `group` | `m8f_group` |
| Engine version | `spiff_serializer_version` | `workflow_engine_version` |

To migrate each area, follow these steps:

1. Pass `datetime.now(UTC)` or another timezone-aware `datetime` wherever you
   passed epoch seconds, and read `created_at`, `started_at`, `ended_at`,
   `run_at`, and `occurred_at` instead of the `*_in_seconds` attributes.
2. Use `work_item_id` in `ClaimTaskCommand` and `CompleteTaskCommand`, and
   read pending work as `WorkItemModel`. Task names, titles, and lanes are
   properties of the work item.
3. Replace `ProcessInstanceEventType` with `ProcessLifecycleEventType` or
   `TaskEventType`. The event string values are unchanged.
4. Store tenant identity in the named user fields.
5. Recreate permission grants as resource pairs, as described in
   [Customize authorization](customize-authorization.md). The built-in roles
   from `ensure_v1_role` already use them.
6. If you call `resolve_lane_assignment_id`, pass `tenant_id`, so lane groups
   don't collide across tenants.

## Migrate the database

The library's Alembic history moves the schema to 1.0. Its head is
`k2l3m4n5o6p7`. On the way, it does the following:

- `d2e4f6a8b0c1` adds the timezone-aware columns and backfills them from the
  epoch columns.
- `e5f6a7b8c9d0` makes `json_data` tenant-scoped, keyed by
  `(m8f_tenant_id, hash)`. It copies a payload shared by several tenants once
  per tenant.
- `f7a8b9c0d1e2` removes the legacy `*_in_seconds` columns.
- Later revisions create `work_item`, split the event types, rename
  constraints into the `m8f_*` namespace, and finish the authorization
  targets.

Every data migration validates first and stops before changing anything if it
finds data it can't convert safely. For example: a human task that can't map
to one work item, conflicting identity values, colliding process digests, a
URI permission with no resource pair, or a JSON reference that is orphaned or
crosses tenants.

To migrate, follow these steps:

1. Stop every workflow writer, scheduler, and worker, or put the application
   in maintenance mode. The JSON re-key changes the table's shape and must not
   run alongside payload writes.
2. Record the current revision:

   ```bash
   uv run alembic current
   ```

3. Take and verify a backup. For PostgreSQL, for example:

   ```bash
   pg_dump --format=custom --file=before-m8flow-core-1.0.dump "$DATABASE_URL"
   ```

   For SQLite, stop every writer and copy the database file while it's closed.
4. Optional: review the SQL first:

   ```bash
   uv run alembic upgrade head --sql > migration.sql
   ```

5. Run one migration process:

   ```bash
   uv run alembic upgrade head
   uv run alembic current
   ```

   The second command must print `k2l3m4n5o6p7 (head)`.
6. Check that no tenant has duplicate JSON rows. The following query must
   return no rows:

   ```sql
   SELECT m8f_tenant_id, hash, COUNT(*)
   FROM json_data
   GROUP BY m8f_tenant_id, hash
   HAVING COUNT(*) > 1;
   ```

7. For every tenant, check a representative workflow, a task claim, the event
   history, and the scheduler rows through your application.
8. Start the new version of every process. Don't start an older one.

These commands assume a checkout of this repository. If your application owns
its migrations, as the [sample app](../../sample_app/README.md) does, apply the
equivalent revisions through your own Alembic history.

### Upgrading m8flow

m8flow runs its own migration chain on top of the library's. Its revision
`d4e5f6a7b8c9_remove_core_epoch_timestamp_columns` is the counterpart of
`f7a8b9c0d1e2`, follows its own compatibility revision `c3d4e5f6a7b8`, and must
run only after the library has reached `k2l3m4n5o6p7`. Deploy the matching
m8flow release and this library together, and record the revision pair in the
m8flow release notes.

## If the upgrade fails

Do the following:

- Don't restart writers against a partly upgraded database.
- Run `uv run alembic current`. If the database supports transactional DDL,
  the failed revision has rolled back on its own.
- If DDL or the staging table `m8f_json_data_tenant_scope_stage` remains,
  restore the backup instead of deleting tables by hand.
- Keep the error message and revision. Fix the data the migration reported,
  restore the backup, and run the whole upgrade again.

## Rolling back

Restore the backup. `alembic downgrade` restores the old schema's shape, but
not its data:

- Removed epoch columns come back empty.
- Tenant-specific JSON rows that share a hash collapse into the
  lexicographically first tenant's copy.

Use `alembic downgrade <revision>` only for controlled development or
recovery, after another backup and with every writer stopped.

## Related pages

- [Database schema](../reference/database-schema.md)
- [About the workflow data model](../explanation/workflow-data-model.md)
- [Changelog](../../CHANGELOG.md)
