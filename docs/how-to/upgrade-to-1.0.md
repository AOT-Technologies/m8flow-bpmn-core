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
| Event types | `ProcessInstanceEventType` | `ProcessLifecycleEventType` and `TaskEventType`, with a non-null `category`. The string values of the 10 kept types are unchanged; seven retired types can't be recorded any more |
| Event time | `timestamp` | `occurred_at` |
| Process digest | `single_process_hash` and `full_process_model_hash` | `process_xml_digest` |
| Scheduler | `run_due_scheduler_jobs(now_in_seconds=...)` | `run_due_scheduler_jobs(now=...)` |
| User identity | `tenant_specific_field_1`, `_2`, `_3` | `realm_identifier`, `external_org_id`, `external_user_id` |
| Authorization | URI targets such as `/tasks/%` | Exact `(resource_type, resource_id)` targets |
| Group table | `group` | `m8f_group` |
| Engine version | `spiff_serializer_version` | `workflow_engine_version` |
| Status helpers | `ProcessInstanceModel.can_submit_task()`, `allowed_to_run()`, `can_receive_message()`, `is_immediately_runnable()`, `terminal_statuses()`, `non_terminal_statuses()`, `immediately_runnable_statuses()` | `has_terminal_status()` or `active_statuses()`, or compare `status` with `ProcessInstanceStatus` directly |
| Future task rows | `FutureTaskModel.insert_or_update(...)` | Load the `future_task` row by `guid` with `session.get(...)`, then add a new `FutureTaskModel` or set attributes on the existing one |
| Claiming | `ClaimTaskCommand(..., added_by=...)` and `claim_task(..., added_by=...)` | No `added_by`; fields are `tenant_id`, `work_item_id`, `user_id` |
| Initiator lanes | A lane whose name looks like "process initiator" goes to the initiator | Only lanes listed in `properties_json["initiator_lanes"]` go to the initiator |
| Potential owner labels | `WorkItemUserAddedBy` includes `guest` and `manual` | Three values: `process_initiator`, `lane_owner`, `lane_assignment` |

To migrate each area, follow these steps:

1. Pass `datetime.now(UTC)` or another timezone-aware `datetime` wherever you
   passed epoch seconds, and read `created_at`, `started_at`, `ended_at`,
   `run_at`, and `occurred_at` instead of the `*_in_seconds` attributes.
2. Use `work_item_id` in `ClaimTaskCommand` and `CompleteTaskCommand`, and
   read pending work as `WorkItemModel`. Task names, titles, and lanes are
   properties of the work item.
3. Replace `ProcessInstanceEventType` with `ProcessLifecycleEventType` or
   `TaskEventType`. The string values of the 10 kept types are unchanged. The
   types `process_instance_force_run`, `process_instance_migrated`,
   `process_instance_rewound_to_task`, `process_instance_suspended_for_error`,
   `task_data_edited`, `task_executed_manually`, and `task_skipped` can't be
   recorded any more; recording one raises `ValidationError`. Existing rows
   that hold them are kept and read back as stored.
4. Store tenant identity in the named user fields.
5. Recreate permission grants as resource pairs, as described in
   [Customize authorization](customize-authorization.md). The built-in roles
   from `ensure_v1_role` already use them.
6. If you call `resolve_lane_assignment_id`, pass `tenant_id`, so lane groups
   don't collide across tenants.
7. Remove `added_by` from every `ClaimTaskCommand` and `claim_task` call.
   Passing it raises `TypeError`.
8. A lane is no longer routed to the process initiator because of its name,
   such as `Process Initiator`. Add every lane whose tasks should go to the
   initiator to the definition's `properties_json["initiator_lanes"]` and
   re-import it. Tasks with no lane still go to the initiator.
9. The library no longer writes the `guest` or `manual` labels, and writing
   them through `WorkItemUserModel.added_by` raises `ValidationError`. If you
   add potential owners yourself, leave `added_by` as `None` or use the label
   that matches the reason. Existing rows with old labels still load and work.

## Migrate the database

The library's Alembic history in 1.0 is a single baseline revision,
`k2l3m4n5o6p7`, with no parent. It creates the complete 1.0 schema on an
empty database. It doesn't convert a 0.x schema or its data: the 0.x
revisions aren't part of 1.0.

Pick the path that matches your database:

| Database | What to do |
|---|---|
| Empty | Run `alembic upgrade head`. See [Create a new database](#create-a-new-database). |
| Version row already reads `k2l3m4n5o6p7` | Don't run or stamp anything. Alembic already treats the database as current. Run `alembic check` to confirm the schema matches. |
| 0.x schema | Convert it with the 0.x migrations and your own migrations, then stamp. See [Convert a 0.x database](#convert-a-0x-database). |

### Create a new database

Run the baseline against the empty database, then confirm the revision:

```bash
uv run alembic upgrade head
uv run alembic current
```

The second command must print `k2l3m4n5o6p7 (head)`.

### Check a database that is already at `k2l3m4n5o6p7`

The baseline keeps the revision ID `k2l3m4n5o6p7`. If a database's
`alembic_version` row already holds that value, `alembic upgrade head` has
nothing to do, and `alembic stamp` isn't needed.

The revision ID alone doesn't prove the tables match the 1.0 models. A
database built before the baseline can still differ in details such as extra
columns, index names, or nullability. To list any differences, run the
following command:

```bash
uv run alembic check
```

If it reports differences, fix them through your own migrations, then run it
again until it reports none.

### Convert a 0.x database

A 0.x database needs the schema and data changes listed in
[Update your code for the new API](#update-your-code-for-the-new-api), such as
timezone-aware timestamps, tenant-scoped `json_data`, `work_item`, split event
types, and resource-pair permission targets. 1.0 doesn't ship these steps.
Your application's own migrations apply them, as m8flow's migration history
does.

To convert, follow these steps:

1. Stop every workflow writer, scheduler, and worker, or put the application
   in maintenance mode.
2. Take and verify a backup. For PostgreSQL, for example:

   ```bash
   pg_dump --format=custom --file=before-m8flow-core-1.0.dump "$DATABASE_URL"
   ```

   For SQLite, stop every writer and copy the database file while it's closed.
3. With the 0.x release still installed, bring the database to the 0.x head
   with that release's migrations, or with your application's migrations if
   it owns them:

   ```bash
   uv run alembic upgrade head
   ```

4. Apply your application's migrations that convert the 0.x schema and data
   to the 1.0 schema.
5. Install 1.0, then compare the converted schema with the 1.0 models. The
   check reads the models, not the revision history, so it works before the
   stamp. The command must report no differences:

   ```bash
   uv run alembic check
   ```

6. Record the 1.0 baseline as applied, without running it:

   ```bash
   uv run alembic stamp k2l3m4n5o6p7
   uv run alembic current
   ```

   The second command must print `k2l3m4n5o6p7 (head)`.
7. For every tenant, check a representative workflow, a task claim, the event
   history, and the scheduler rows through your application.
8. Start the new version of every process. Don't start an older one.

These commands assume a checkout of this repository. If your application owns
its migrations, as the [sample app](../../sample_app/README.md) does, run the
equivalent commands against your own Alembic environment.

### Upgrading m8flow

m8flow runs its own migration chain alongside the library's, and it expects
the library at `k2l3m4n5o6p7`. Deploy the matching m8flow release and this
library together. m8flow's migrations must leave the library tables in the
1.0 schema before the library's version row reads `k2l3m4n5o6p7`.

## If the upgrade fails

Do the following:

- Don't restart writers against a partly converted database.
- Run `uv run alembic current` and keep the error message.
- Restore the backup instead of fixing tables by hand. Fix the cause, then run
  the whole conversion again.

## Rolling back

Restore the backup. `alembic downgrade base` drops every library table and
its data, so it can't take a 1.0 database back to 0.x. Use it only to reset a
development database.

## Related pages

- [Database schema](../reference/database-schema.md)
- [About the workflow data model](../explanation/workflow-data-model.md)
- [Changelog](../../CHANGELOG.md)
