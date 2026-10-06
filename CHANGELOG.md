# Changelog

## 0.2.0 — Breaking release

This release removes legacy epoch timestamp persistence and command parameters.
Consumers must migrate to timezone-aware UTC `datetime` values before applying
the `f7a8b9c0d1e2_remove_legacy_epoch_columns` Alembic migration.

Breaking changes:

- Replaced persisted `*_in_seconds` timestamp columns with timezone-aware
  `DateTime` columns such as `created_at`, `updated_at`, `run_at`, and
  `occurred_at`.
- Replaced public timestamp parameters such as `started_at_in_seconds` and
  `completed_at_in_seconds` with `started_at` and `completed_at`.
- Updated scheduler APIs and documentation to use datetime values.
- Removed legacy timestamp synchronization and ORM mappings.

Other changes:

- Tenant-scoped JSON storage now uses `(m8f_tenant_id, hash)`.
- Added normalized `work_item` claim-state persistence.
- Added split lifecycle/task event enums and event categories.
- Hardened authorization target identity and concurrent row creation.

See [`doc/compatibility.md`](doc/compatibility.md) and
[`doc/migration_runbook.md`](doc/migration_runbook.md) before upgrading.
