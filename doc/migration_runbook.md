# Schema migration runbook

This runbook covers the tenant-safe JSON and timestamp migrations in the
current Alembic history. The host application owns the production migration
runner; the commands below are examples for a checkout that contains the
repository migrations.

## Before upgrading

1. Stop workflow writers and scheduler workers, or place the host application
   in maintenance mode. The JSON re-key changes the table shape and must not
   run concurrently with payload writes.
2. Record the current revision:

   ```bash
   uv run alembic current
   ```

3. Take a tested database backup. For PostgreSQL, use a logical or physical
   backup appropriate to the deployment, for example:

   ```bash
   pg_dump --format=custom --file=m8flow-before-0.1.1.dump "$DATABASE_URL"
   ```

   For SQLite, stop all writers and copy the database file while it is closed.
   A backup is the authoritative rollback mechanism for data-preserving
   recovery.
4. Review the generated SQL for the target database where practical:

   ```bash
   uv run alembic upgrade head --sql > migration.sql
   ```

5. Confirm that the database contains the tenant rows referenced by
   `bpmn_process.m8f_tenant_id` and `task.m8f_tenant_id`. The phase-4 migration
   performs this check again and aborts if it finds missing tenants, missing
   payloads, null references, or unreferenced legacy `json_data` rows.

## Upgrade

Run one migration process against the database:

```bash
uv run alembic upgrade head
uv run alembic current
```

The JSON migration first validates all process and task references, including
`task.python_env_data_hash`. It then stages the complete tenant-qualified
dataset, replaces the legacy hash-only table, and creates the composite key
`(m8f_tenant_id, hash)`. A shared legacy hash is copied once per referenced
tenant; task-only payloads are retained.

After the upgrade, verify the revision and key shape:

```sql
SELECT version_num FROM alembic_version;
SELECT m8f_tenant_id, hash, COUNT(*)
FROM json_data
GROUP BY m8f_tenant_id, hash
HAVING COUNT(*) > 1;
```

The second query must return no rows. Spot-check a process and task from each
tenant through the application API, and confirm that equal hashes in different
tenants resolve to tenant-local rows.

## Failed upgrade recovery

- Do not resume application writers against a partially upgraded database.
- If the migration is inside a transactional DDL boundary, the failed
  revision should roll back automatically. Confirm with `alembic current`.
- If the database engine leaves DDL or the staging table behind, restore the
  pre-upgrade backup instead of manually deleting migration tables. The phase-4
  staging table is named `m8f_json_data_tenant_scope_stage`.
- Preserve the migration error and database revision for diagnosis. Resolve
  the reported orphan, missing tenant, or missing payload, restore the backup,
  and rerun the complete upgrade.

## Downgrade and rollback limits

The preferred rollback is restoring the tested pre-upgrade backup. An Alembic
downgrade can restore the old schema shape, but it is not lossless for data
that became tenant-specific: the phase-4 downgrade collapses duplicate
tenant-local rows for the same hash to the lexicographically first tenant so
the legacy hash-only primary key can be recreated.

If a downgrade is explicitly required for a controlled development or recovery
operation:

```bash
uv run alembic downgrade <known-good-revision>
```

Take another backup first, stop all writers, and verify the resulting schema
and tenant data before restarting the application. Do not use downgrade as a
substitute for restoring production data after a failed upgrade.

## Compatibility expectations

- Existing epoch timestamp inputs remain accepted by the public API.
- Native timezone-aware timestamp attributes are populated and should be used
  for new reads.
- JSON payload access must always use both tenant id and hash.
- A successful migration must leave no staging table and must report the new
  Alembic head revision.
