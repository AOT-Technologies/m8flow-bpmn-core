# Database schema

This page lists the tables that m8flow-core's ORM models define. The library
ships the models in `m8flow_core.models` and Alembic revisions in
`alembic/versions/`. The immutable baseline, `k2l3m4n5o6p7`, has no parent.
Revision `l3m4n5o6p7q8` adds the signal registry and is the current head.
The baseline builds the 1.0 schema on an empty database; see [Upgrade to 1.0](../how-to/upgrade-to-1.0.md) for existing
databases.

Table and column names are part of the compatibility contract, because host
applications such as m8flow share the database. Renaming or removing one is a
breaking change. Don't write to these tables directly; use the
[public API](api.md).

## Conventions

Every table follows these conventions:

- Timestamps are timezone-aware `DateTime` columns that hold UTC values, named
  `created_at`, `updated_at`, `started_at`, `ended_at`, `run_at`, or
  `occurred_at`. No column stores epoch seconds.
- Tenant-owned tables have an `m8f_tenant_id` column, a foreign key to
  `m8flow_tenant.id`. Every library read and write filters on it.
- Constraint and index names use the `m8f_` prefix.

## Tables

The library defines the following tables:

| Table | Model | Tenant-owned | Holds |
|---|---|---|---|
| `m8flow_tenant` | `M8flowTenantModel` | No | Tenants: ID, name, slug, and status |
| `user` | `UserModel` | No | Users, with `service`, `service_id`, and the identity fields `realm_identifier`, `external_org_id`, and `external_user_id` |
| `m8f_group` | `GroupModel` | No | Groups, scoped to a tenant by a `<tenant>:<name>` identifier |
| `user_group_assignment` | `UserGroupAssignmentModel` | No | Group membership |
| `principal` | `PrincipalModel` | No | The user or group that a permission is granted to |
| `permission_target` | `PermissionTargetModel` | No | A command and a resource: `command`, `resource_type`, and an optional `resource_id` |
| `permission_assignment` | `PermissionAssignmentModel` | No | A grant of a target to a principal, with a permission and `permit` or `deny` |
| `bpmn_process_definition` | `BpmnProcessDefinitionModel` | Yes | Imported BPMN and DMN source and properties, such as `lane_owners` and `initiator_lanes` |
| `process_model_bpmn_version` | `ProcessModelBpmnVersionModel` | Yes | Versions of a process model's BPMN |
| `process_instance` | `ProcessInstanceModel` | Yes | Process instances: status, initiator, timestamps, and `workflow_engine_version` |
| `bpmn_process` | `BpmnProcessModel` | Yes | The serialized workflow of a process instance |
| `task_definition` | `TaskDefinitionModel` | Yes | BPMN task specifications |
| `task` | `TaskModel` | Yes | Runtime task state, using SpiffWorkflow state names such as `READY` and `COMPLETED` |
| `work_item` | `WorkItemModel` | Yes | Claim state of a human task: lane, owner, completing user, status, and `completed` |
| `work_item_user` | `WorkItemUserModel` | Yes | The potential owners of a work item. The library writes `added_by` as `process_initiator`, `lane_owner`, or `lane_assignment`; hosts may also write `NULL`. Other values already stored, such as `guest` or `manual`, are read back as-is |
| `json_data` | `JsonDataModel` | Yes | Process and task data payloads, keyed by `(m8f_tenant_id, hash)` |
| `process_instance_event` | `ProcessInstanceEventModel` | Yes | Event history: `event_type`, `category`, `occurred_at`, and the acting user. `event_type` has no database constraint, so historical values stay readable |
| `process_instance_metadata` | `ProcessInstanceMetadataModel` | Yes | Key and value pairs, including task payload values |
| `scheduler_job` | `SchedulerJobModel` | Yes | Due timers and retries. [About scheduling](../explanation/scheduling.md#the-scheduler-job-table) lists its columns. |
| `future_task` | `FutureTaskModel` | Yes | Runtime tasks waiting to run, with `run_at` and `queued_to_run_at` |

## Signal registry

All four signal tables have tenant-qualified ownership and remain in the
caller's transaction. Publications and receipts retain audit history when
subscriptions retire. Foreign keys from receipts to publications/subscriptions
include `m8f_tenant_id`, preventing cross-tenant associations. Subscription
references to definitions/instances and receipt references to resulting instances
also use tenant-qualified foreign keys. The migration adds composite unique
indexes on `(m8f_tenant_id, id)` to those two existing parent tables.

| Table | Stable columns |
|---|---|
| `signal_registry_state` | `m8f_tenant_id` (primary key), `version` |
| `signal_publication` | `id`, `m8f_tenant_id`, `occurrence_id`, `signal_name`, `payload_json`, `source_process_instance_id`, `source_task_guid`, `published_by_user_id`, `published_at` |
| `signal_subscription` | `id`, `m8f_tenant_id`, `subscription_key`, `signal_name`, `kind`, `process_instance_id`, `definition_id`, `scope_id`, `task_guid`, `task_spec_name`, `process_model_identifier`, `repeatable`, `active`, `reserved`, `created_at` |
| `signal_delivery` | `id`, `m8f_tenant_id`, `publication_id`, `subscription_id`, `status`, `attempts`, `retry_at`, `delivered_at`, `last_error`, `process_instance_id` |

Occurrence IDs are unique per tenant. Subscription keys identify an activation
or persistent handler within the tenant. Receipt uniqueness is
`(m8f_tenant_id, publication_id, subscription_id)`. Subscription `kind` is
`start`, `catch`, `boundary`, or `event_subprocess`. Receipt `status` is
`pending`, `delivered`, or `skipped`. All timestamps use UTC datetimes.
`payload_json` accepts JSON null and arbitrary JSON values. Source identifiers
are retained audit references; they are not cascading foreign keys.

## Tenant-scoped JSON payloads

`json_data` has the composite primary key `(m8f_tenant_id, hash)`, so equal
payloads in two tenants are two rows. Three columns refer to it by hash:

| Table | Column |
|---|---|
| `bpmn_process` | `json_data_hash` |
| `task` | `json_data_hash` |
| `task` | `python_env_data_hash` |

These references have no database foreign key. The library always resolves
them with the referencing row's tenant and the hash together.

## Related pages

- [Upgrade to 1.0](../how-to/upgrade-to-1.0.md): what changed from 0.x
- [About the workflow data model](../explanation/workflow-data-model.md)
- [Public API reference](api.md)
