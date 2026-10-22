# Database schema

This page lists the tables that m8flow-core's ORM models define. The library
ships the models in `m8flow_core.models` and the Alembic migrations that
create them in `alembic/versions/`. Its current head is `k2l3m4n5o6p7`.

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
| `bpmn_process_definition` | `BpmnProcessDefinitionModel` | Yes | Imported BPMN and DMN source and properties, such as `lane_owners` |
| `process_model_bpmn_version` | `ProcessModelBpmnVersionModel` | Yes | Versions of a process model's BPMN |
| `process_instance` | `ProcessInstanceModel` | Yes | Process instances: status, initiator, timestamps, and `workflow_engine_version` |
| `bpmn_process` | `BpmnProcessModel` | Yes | The serialized workflow of a process instance |
| `task_definition` | `TaskDefinitionModel` | Yes | BPMN task specifications |
| `task` | `TaskModel` | Yes | Runtime task state, using SpiffWorkflow state names such as `READY` and `COMPLETED` |
| `work_item` | `WorkItemModel` | Yes | Claim state of a human task: lane, owner, completing user, status, and `completed` |
| `work_item_user` | `WorkItemUserModel` | Yes | The potential owners of a work item |
| `json_data` | `JsonDataModel` | Yes | Process and task data payloads, keyed by `(m8f_tenant_id, hash)` |
| `process_instance_event` | `ProcessInstanceEventModel` | Yes | Event history: `event_type`, `category`, `occurred_at`, and the acting user |
| `process_instance_metadata` | `ProcessInstanceMetadataModel` | Yes | Key and value pairs, including task payload values |
| `scheduler_job` | `SchedulerJobModel` | Yes | Due timers and retries. [About scheduling](../explanation/scheduling.md#the-scheduler-job-table) lists its columns. |
| `future_task` | `FutureTaskModel` | Yes | Runtime tasks waiting to run, with `run_at` and `queued_to_run_at` |

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
