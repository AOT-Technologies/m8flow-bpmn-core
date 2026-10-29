# Customize authorization

This guide shows how to grant users permission to run workflow commands, and
how to add your own authorization rules on top of the built-in ones. For how
the checks work, see
[About authorization and tenancy](../explanation/authorization-and-tenancy.md).

## Grant a built-in role

The library has three built-in roles per tenant. Each grants a fixed set of
command keys:

| Role | Command keys |
|---|---|
| `user` | `process.start`, `task.claim`, `task.complete` |
| `manager` | `task.claim`, `task.complete` |
| `admin` | `process_definition.import`, `process.start`, `task.claim`, `task.complete`, `process.suspend`, `process.resume`, `process.retry`, `process.terminate` |

To give users a role, call `ensure_v1_role` in your own transaction:

```python
from m8flow_core.services.authorization import ROLE_MANAGER, ensure_v1_role

ensure_v1_role(
    session,
    tenant_id="acme",
    role_name=ROLE_MANAGER,
    user_ids=[finance_user.id, compliance_user.id],
)
```

The function creates the tenant's role group and its permission grants if
they don't exist, and adds the users to the group. It's safe to call more than
once.

## Grant a permission on one resource

A built-in role applies to its whole tenant. To grant a command on a single
resource instead, add the user to a tenant-scoped group and grant that group a
permission on the resource. The following example lets members of the
`expense-starters` group start the `expense-approval` process model, and no
other:

```python
from m8flow_core import api
from m8flow_core.services.authorization import (
    add_user_to_group,
    grant_permission_to_group,
)

add_user_to_group(
    session,
    user_id=clerk.id,
    group_identifier="acme:expense-starters",
    group_name="expense-starters",
)
grant_permission_to_group(
    session,
    group_identifier="acme:expense-starters",
    permission="start",
    command=api.PROCESS_START_COMMAND,
    resource_type="process_model",
    resource_id="expense-approval",
)
```

The `acme:` prefix scopes the group to the `acme` tenant. The `permission`
must match the command's permission: `start` for `process.start`, `create`
for `process_definition.import`, and `execute` for the task and lifecycle
commands. Pass `resource_id=None` to grant the command on every resource of
that type, or `grant_type="deny"` to block it; a matching deny always wins.

## Add a custom policy

A policy is any object with an `authorize(session, request)` method that
returns an `AuthorizationDecision`. The following policy blocks task
completion in the `Finance` lane and defers every other decision to the
built-in database policy:

```python
from m8flow_core import api


class FinanceGatePolicy:
    def authorize(self, session, request):
        if (
            request.command_key == api.TASK_COMPLETE_COMMAND
            and request.metadata is not None
            and request.metadata.get("lane_name") == "Finance"
        ):
            return api.AuthorizationDecision(
                False,
                reason="Finance completions require external approval",
            )
        return api.DatabaseAuthorizationPolicy().authorize(session, request)
```

When a policy denies a command, the library raises `AuthorizationError` with
the reason.

The `request` carries `tenant_id`, `actor_user_id`, `command_key`,
`permission`, `resource_type`, `resource_id`, and `metadata`. Only
`process.start`, `task.claim`, and `task.complete` fill in `metadata`, with
details such as the task's lane and owners or the process definition
identifiers.

## Install the policy

To install a policy for a block of calls, such as one request or one test,
use `authorization_policy_scope`:

```python
with api.authorization_policy_scope(FinanceGatePolicy()):
    api.execute_command(connection, api.CompleteTaskCommand(...))
```

To install it for every call in the process, register a factory once at
startup:

```python
api.set_default_authorization_policy_factory(FinanceGatePolicy)
```

`authorization_policy_scope` accepts either a policy instance or a factory
that returns one. `set_default_authorization_policy_factory` accepts only a
factory, such as the policy class itself.

## Related pages

- [About authorization and tenancy](../explanation/authorization-and-tenancy.md)
- [Authorization hooks in the API reference](../reference/api.md#authorization-hooks)
- [Limitations](../reference/limitations.md#authorization): the commands
  that aren't permission-checked yet
