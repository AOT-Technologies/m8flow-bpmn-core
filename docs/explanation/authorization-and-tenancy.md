# About authorization and tenancy

Every workflow call in m8flow-core is made in a tenant and, for most
calls, by a user. This page explains how the library decides that a user
belongs to a tenant, and whether they may run a command.

## Checks run in a fixed order

For a user-scoped command, such as `CompleteTaskCommand`, the library runs
three checks, in this order:

1. **Tenant membership.** Is the user in the tenant named by `tenant_id`?
2. **Command permission.** Does the authorization policy allow this user to
   run this command key, such as `task.complete`, in this tenant?
3. **Runtime rules.** Is the user assigned to this task, and do they hold the
   claim on it?

Any failed check raises `AuthorizationError`, and nothing is written. Users
from another tenant fail at the first check, before any permission data is
read.

## How tenant membership works

The library doesn't keep a membership table. It compares identifiers instead:

- A tenant's identifiers are its `id` and its `slug`.
- A user's identifiers are the realm name taken from `user.service`, plus any
  values in `realm_identifier`, `external_org_id`, and `external_user_id`. For a `service` of
  `http://localhost:7002/realms/acme`, the realm name is `acme`.

A user belongs to a tenant when the two sets share a value.

This matches how m8flow stores users from Keycloak. A user from a
per-tenant realm matches through the realm name. A user from a shared realm
has the shared realm in `service`, so the tenant ID and slug go in the
tenant-specific fields instead.

## How command permissions work

Each covered command has a stable **command key**, such as `process.start` or
`task.claim`, and is checked against one **resource**: a `resource_type` and a
`resource_id`. Starting a process checks the `process_model` named by the
definition's identifier; claiming a task checks the `task` with that task's
GUID. The [API reference](../reference/api.md#authorization-hooks) lists the
resource for every command.

Permissions are stored as `permission_target` rows, each naming one command,
one resource type, and an optional resource ID, and `permission_assignment`
rows that grant a target to a user or group. The default policy,
`DatabaseAuthorizationPolicy`, collects the user's assignments, both direct
and through groups, and matches them against the request:

- The assignment's permission, such as `execute` or `start`, must match the
  request's, or be `all`.
- The target's command must equal the request's command key.
- The target's resource must match the request's:
  - A target of type `tenant` matches every resource in that tenant, or in
    any tenant if it has no resource ID.
  - Any other target must have the same resource type. With a resource ID, it
    matches only that resource; without one, it matches every resource of
    that type.
- A matching `deny` wins over any matching `permit`. With no match, the
  request is denied.

Matching is exact: there are no URI patterns or wildcards. A target must name
both its type and, when it has one, a complete resource ID.

Groups are tenant-scoped by their identifier. A group named `acme:admin`
applies only in a tenant whose ID or slug is `acme`. A group identifier with
no `tenant:` prefix applies in every tenant.

## The built-in roles

`ensure_v1_role` creates three role groups per tenant, `user`, `manager`, and
`admin`, each with a fixed set of command grants. Each grant targets the
tenant itself, so a role applies to every process and task in its tenant.
[Customize authorization](../how-to/customize-authorization.md#grant-a-built-in-role)
lists them. They're a starting point, not a full permission model: the
`admin` role, for example, is one coarse workflow operator.

## Replacing the policy

The permission check goes through a policy object, so a host application can
replace or wrap `DatabaseAuthorizationPolicy` without changing any command.
For `process.start`, `task.claim`, and `task.complete`, the request carries
metadata, such as the task's lane and owners or the process definition, so a
custom policy can make decisions the database grants can't express. Tenant
membership and the runtime rules still apply, whatever the policy decides.

[Customize authorization](../how-to/customize-authorization.md#add-a-custom-policy)
shows how.

## Processes started by a timer

A timer start event has no user to act as the initiator. The library creates
those instances with an internal, tenant-scoped system user and stores its ID
in `process_initiator_id`.

## Related pages

- [Customize authorization](../how-to/customize-authorization.md)
- [Authorization hooks in the API reference](../reference/api.md#authorization-hooks)
- [Limitations](../reference/limitations.md#authorization)
