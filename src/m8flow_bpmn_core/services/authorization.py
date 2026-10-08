from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import insert, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from m8flow_bpmn_core.application.commands import (
    ClaimTaskCommand,
    CompleteTaskCommand,
    CreateProcessInstanceCommand,
    ErrorProcessInstanceCommand,
    ImportBpmnProcessDefinitionCommand,
    InitializeProcessInstanceFromDefinitionCommand,
    InitializeProcessInstanceWorkflowCommand,
    RecordProcessInstanceEventCommand,
    ResumeProcessInstanceCommand,
    RetryProcessInstanceCommand,
    ScheduleProcessInstanceRetryCommand,
    SuspendProcessInstanceCommand,
    TerminateProcessInstanceCommand,
    UpsertProcessInstanceMetadataCommand,
)
from m8flow_bpmn_core.errors import AuthorizationError, NotFoundError
from m8flow_bpmn_core.models.group import GroupModel
from m8flow_bpmn_core.models.permission_assignment import (
    PermissionAction,
    PermissionAssignmentModel,
    PermitDeny,
)
from m8flow_bpmn_core.models.permission_target import (
    InvalidPermissionTargetError,
    PermissionResourceType,
    PermissionTargetModel,
)
from m8flow_bpmn_core.models.principal import PrincipalModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.models.user_group_assignment import UserGroupAssignmentModel
from m8flow_bpmn_core.services.tenant_users import tenant_identifiers_for

TASK_CLAIM_COMMAND = "task.claim"
TASK_COMPLETE_COMMAND = "task.complete"
PROCESS_START_COMMAND = "process.start"
PROCESS_CREATE_COMMAND = "process.create"
PROCESS_DEFINITION_IMPORT_COMMAND = "process_definition.import"
PROCESS_WORKFLOW_INITIALIZE_COMMAND = "process.initialize_workflow"
PROCESS_METADATA_UPSERT_COMMAND = "process.metadata.upsert"
PROCESS_EVENT_RECORD_COMMAND = "process.event.record"
PROCESS_SUSPEND_COMMAND = "process.suspend"
PROCESS_RESUME_COMMAND = "process.resume"
PROCESS_ERROR_COMMAND = "process.error"
PROCESS_RETRY_COMMAND = "process.retry"
PROCESS_TERMINATE_COMMAND = "process.terminate"

RESOURCE_TASK = PermissionResourceType.task
RESOURCE_PROCESS_DEFINITION = PermissionResourceType.process_definition
RESOURCE_PROCESS_INSTANCE = PermissionResourceType.process_instance
RESOURCE_PROCESS_MODEL = PermissionResourceType.process_model
RESOURCE_TENANT = PermissionResourceType.tenant

ROLE_USER = "user"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"
BASIC_ROLE_NAMES = frozenset({ROLE_USER, ROLE_MANAGER, ROLE_ADMIN})

@dataclass(frozen=True, slots=True)
class CommandAuthorizationSpec:
    command_key: str
    permission: str
    resource_type: PermissionResourceType
    actor_field_name: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    tenant_id: str
    actor_user_id: int
    command_key: str
    permission: str
    resource_type: PermissionResourceType
    resource_id: str
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resource_type",
            PermissionResourceType(self.resource_type).value,
        )


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str | None = None


@runtime_checkable
class AuthorizationPolicy(Protocol):
    def authorize(
        self,
        session: Session,
        request: AuthorizationRequest,
    ) -> AuthorizationDecision: ...


AuthorizationPolicyFactory = Callable[[], AuthorizationPolicy]


COMMAND_AUTHORIZATION_SPECS: dict[type[object], CommandAuthorizationSpec] = {
    ClaimTaskCommand: CommandAuthorizationSpec(
        command_key=TASK_CLAIM_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_TASK,
        actor_field_name="user_id",
    ),
    CompleteTaskCommand: CommandAuthorizationSpec(
        command_key=TASK_COMPLETE_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_TASK,
        actor_field_name="user_id",
    ),
    CreateProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_CREATE_COMMAND,
        permission=PermissionAction.create.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="process_initiator_id",
    ),
    ImportBpmnProcessDefinitionCommand: CommandAuthorizationSpec(
        command_key=PROCESS_DEFINITION_IMPORT_COMMAND,
        permission=PermissionAction.create.value,
        resource_type=RESOURCE_PROCESS_DEFINITION,
        actor_field_name="user_id",
    ),
    InitializeProcessInstanceFromDefinitionCommand: CommandAuthorizationSpec(
        command_key=PROCESS_START_COMMAND,
        permission=PermissionAction.start.value,
        resource_type=RESOURCE_PROCESS_MODEL,
        actor_field_name="process_initiator_id",
    ),
    InitializeProcessInstanceWorkflowCommand: CommandAuthorizationSpec(
        command_key=PROCESS_WORKFLOW_INITIALIZE_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
    ),
    UpsertProcessInstanceMetadataCommand: CommandAuthorizationSpec(
        command_key=PROCESS_METADATA_UPSERT_COMMAND,
        permission=PermissionAction.update.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
    ),
    RecordProcessInstanceEventCommand: CommandAuthorizationSpec(
        command_key=PROCESS_EVENT_RECORD_COMMAND,
        permission=PermissionAction.create.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="user_id",
    ),
    SuspendProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_SUSPEND_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="user_id",
    ),
    ResumeProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_RESUME_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="user_id",
    ),
    ErrorProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_ERROR_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="user_id",
    ),
    RetryProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_RETRY_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="user_id",
    ),
    ScheduleProcessInstanceRetryCommand: CommandAuthorizationSpec(
        command_key=PROCESS_RETRY_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="user_id",
    ),
    TerminateProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_TERMINATE_COMMAND,
        permission=PermissionAction.execute.value,
        resource_type=RESOURCE_PROCESS_INSTANCE,
        actor_field_name="user_id",
    ),
}

COMMAND_AUTHORIZATION_SPECS_BY_KEY = {
    spec.command_key: spec for spec in COMMAND_AUTHORIZATION_SPECS.values()
}

V1_BASIC_ROLE_COMMAND_KEYS: dict[str, tuple[str, ...]] = {
    ROLE_USER: (
        PROCESS_START_COMMAND,
        TASK_CLAIM_COMMAND,
        TASK_COMPLETE_COMMAND,
    ),
    ROLE_MANAGER: (
        TASK_CLAIM_COMMAND,
        TASK_COMPLETE_COMMAND,
    ),
    ROLE_ADMIN: (
        PROCESS_START_COMMAND,
        TASK_CLAIM_COMMAND,
        TASK_COMPLETE_COMMAND,
        PROCESS_DEFINITION_IMPORT_COMMAND,
        PROCESS_SUSPEND_COMMAND,
        PROCESS_RESUME_COMMAND,
        PROCESS_RETRY_COMMAND,
        PROCESS_TERMINATE_COMMAND,
    ),
}

_DEFAULT_POLICY_FACTORY: AuthorizationPolicyFactory
_ACTIVE_POLICY_FACTORY: ContextVar[AuthorizationPolicyFactory | None] = ContextVar(
    "m8flow_bpmn_core_authorization_policy_factory",
    default=None,
)


class DatabaseAuthorizationPolicy:
    def authorize(
        self,
        session: Session,
        request: AuthorizationRequest,
    ) -> AuthorizationDecision:
        user = session.get(UserModel, request.actor_user_id)
        if user is None:
            raise NotFoundError(f"User {request.actor_user_id} was not found")

        tenant_identifiers = tenant_identifiers_for(session, request.tenant_id)
        matching_assignments = [
            assignment
            for assignment in permission_assignments_for_user(
                session,
                user_id=user.id,
                tenant_identifiers=tenant_identifiers,
            )
            if permission_assignment_matches_request(assignment, request)
        ]

        if any(
            assignment.grant_type == PermitDeny.deny.value
            for assignment in matching_assignments
        ):
            return AuthorizationDecision(
                allowed=False,
                reason=(
                    "A deny permission matched "
                    f"{request.command_key} on "
                    f"{request.resource_type}:{request.resource_id}"
                ),
            )

        if any(
            assignment.grant_type == PermitDeny.permit.value
            for assignment in matching_assignments
        ):
            return AuthorizationDecision(allowed=True)

        return AuthorizationDecision(
            allowed=False,
            reason=(
                "No matching permission grant was found for "
                f"{request.command_key} on "
                f"{request.resource_type}:{request.resource_id}"
            ),
        )


_DEFAULT_POLICY_FACTORY = DatabaseAuthorizationPolicy


def authorization_spec_for_command(command: object) -> CommandAuthorizationSpec:
    command_type = type(command)
    spec = COMMAND_AUTHORIZATION_SPECS.get(command_type)
    if spec is None:
        raise TypeError(f"Unsupported command type for authorization: {command_type!r}")
    return spec


def authorization_spec_for_command_key(command_key: str) -> CommandAuthorizationSpec:
    spec = COMMAND_AUTHORIZATION_SPECS_BY_KEY.get(command_key)
    if spec is None:
        raise KeyError(f"Unknown command key: {command_key}")
    return spec


def actor_user_id_from_command(command: object) -> int | None:
    spec = authorization_spec_for_command(command)
    if spec.actor_field_name is None:
        return None

    actor_user_id = getattr(command, spec.actor_field_name)
    if actor_user_id is None:
        return None
    if not isinstance(actor_user_id, int):
        raise TypeError(
            f"Command actor field {spec.actor_field_name!r} is not an int: "
            f"{actor_user_id!r}"
        )
    return actor_user_id


def build_authorization_request(
    *,
    tenant_id: str,
    actor_user_id: int,
    command_key: str,
    permission: str | None = None,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    metadata: Mapping[str, object] | None = None,
) -> AuthorizationRequest:
    spec = authorization_spec_for_command_key(command_key)
    return AuthorizationRequest(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        command_key=command_key,
        permission=permission or spec.permission,
        resource_type=PermissionResourceType(resource_type or spec.resource_type),
        resource_id=str(resource_id if resource_id is not None else tenant_id),
        metadata=metadata,
    )


def resolve_authorization_policy(
    policy: AuthorizationPolicy | None = None,
) -> AuthorizationPolicy:
    if policy is not None:
        return policy

    active_factory = _ACTIVE_POLICY_FACTORY.get()
    factory = active_factory or _DEFAULT_POLICY_FACTORY
    return factory()


def set_default_authorization_policy_factory(
    factory: AuthorizationPolicyFactory,
) -> None:
    global _DEFAULT_POLICY_FACTORY
    _DEFAULT_POLICY_FACTORY = factory


@contextmanager
def authorization_policy_scope(
    policy_or_factory: AuthorizationPolicy | AuthorizationPolicyFactory,
) -> Iterator[None]:
    if isinstance(policy_or_factory, AuthorizationPolicy):
        def factory() -> AuthorizationPolicy:
            return policy_or_factory
    else:
        factory = policy_or_factory

    token = _ACTIVE_POLICY_FACTORY.set(factory)
    try:
        yield
    finally:
        _ACTIVE_POLICY_FACTORY.reset(token)


def require_command_authorization(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: int,
    command_key: str,
    permission: str | None = None,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    metadata: Mapping[str, object] | None = None,
    policy: AuthorizationPolicy | None = None,
) -> None:
    request = build_authorization_request(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        command_key=command_key,
        permission=permission,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
    )
    decision = resolve_authorization_policy(policy).authorize(session, request)
    if decision.allowed:
        return

    raise AuthorizationError(
        f"User {actor_user_id} is not authorized for {command_key} in tenant "
        f"{tenant_id}: {decision.reason or 'permission denied'}"
    )


def permission_assignments_for_user(
    session: Session,
    *,
    user_id: int,
    tenant_identifiers: set[str],
) -> list[PermissionAssignmentModel]:
    group_principal_ids = [
        principal_id
        for principal_id, group_identifier in session.execute(
            select(PrincipalModel.id, GroupModel.identifier)
            .join(GroupModel, PrincipalModel.group_id == GroupModel.id)
            .join(
                UserGroupAssignmentModel,
                UserGroupAssignmentModel.group_id == GroupModel.id,
            )
            .where(UserGroupAssignmentModel.user_id == user_id)
        ).all()
        if group_identifier_applies_to_tenant(
            group_identifier,
            tenant_identifiers=tenant_identifiers,
        )
    ]

    principal_filters = [PrincipalModel.user_id == user_id]
    if group_principal_ids:
        principal_filters.append(PrincipalModel.id.in_(group_principal_ids))

    principal_ids = list(
        session.scalars(
            select(PrincipalModel.id).where(or_(*principal_filters))
        ).all()
    )
    if not principal_ids:
        return []

    return list(
        session.scalars(
            select(PermissionAssignmentModel).where(
                PermissionAssignmentModel.principal_id.in_(principal_ids)
            )
        ).all()
    )


def permission_assignment_matches_request(
    assignment: PermissionAssignmentModel,
    request: AuthorizationRequest,
) -> bool:
    permission_target = assignment.permission_target
    if permission_target is None:
        return False

    if assignment.permission not in {
        request.permission,
        PermissionAction.all.value,
    }:
        return False

    target_command = permission_target.command
    if target_command is not None and target_command != request.command_key:
        return False

    target_resource_type = _normalize_resource_component(
        permission_target.resource_type
    )
    target_resource_id = _normalize_resource_component(permission_target.resource_id)
    request_resource_type = _normalize_resource_component(request.resource_type)
    request_resource_id = _normalize_resource_component(request.resource_id)
    if target_resource_type is None or request_resource_type is None:
        return False
    if target_resource_type == RESOURCE_TENANT:
        return target_resource_id is None or target_resource_id == request.tenant_id
    if target_resource_type != request_resource_type:
        return False
    # A null target id is an explicit type-wide grant. It is not a URI
    # wildcard and does not require Python glob or regex matching.
    return target_resource_id is None or target_resource_id == request_resource_id


def _normalize_resource_component(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def group_identifier_applies_to_tenant(
    group_identifier: str | None,
    *,
    tenant_identifiers: set[str],
) -> bool:
    if not isinstance(group_identifier, str):
        return False

    normalized_identifier = group_identifier.strip()
    if not normalized_identifier:
        return False

    tenant_prefix, separator, _rest = normalized_identifier.partition(":")
    if not separator:
        return True
    return tenant_prefix in tenant_identifiers


def tenant_role_group_identifier(tenant_id: str, role_name: str) -> str:
    return f"{tenant_id.strip()}:{role_name.strip()}"


def _get_or_create[ModelT](
    session: Session,
    model: type[ModelT],
    *,
    lookup: Mapping[str, object],
    values: Mapping[str, object],
    conflict_columns: Sequence[str] = (),
) -> ModelT:
    """Return a row using explicit values and a conflict-safe insert.

    Values are supplied by the caller instead of being extracted from an
    unflushed ORM instance. This preserves SQLAlchemy Python-side defaults by
    omitting columns that the database or ORM should populate. The conflict
    action never updates an existing row.
    """
    existing = session.scalar(select(model).filter_by(**lookup))
    if existing is not None:
        return existing

    table = model.__table__  # type: ignore[attr-defined]
    dialect_name = session.get_bind().dialect.name
    statement: Any
    if dialect_name == "postgresql":
        statement = postgresql_insert(model).values(**dict(values))
    elif dialect_name in {"mysql", "mariadb"}:
        statement = mysql_insert(model).values(**dict(values))
    elif dialect_name == "sqlite":
        statement = sqlite_insert(model).values(**dict(values))
    else:
        # Keep unsupported dialects usable while requiring their unique
        # constraints to arbitrate concurrent calls.
        statement = insert(model).values(**dict(values))

    if dialect_name in {"postgresql", "sqlite"}:
        conflict_kwargs = (
            {"index_elements": list(conflict_columns)}
            if conflict_columns
            else {}
        )
        session.execute(statement.on_conflict_do_nothing(**conflict_kwargs))
    elif dialect_name in {"mysql", "mariadb"}:
        primary_key = next(iter(table.primary_key.columns))
        session.execute(
            statement.on_duplicate_key_update(
                **{primary_key.key: primary_key},
            )
        )
    else:
        try:
            with session.begin_nested():
                session.execute(statement)
        except IntegrityError:
            # A concurrent transaction may have won the unique constraint;
            # reload below after the savepoint rolls back this insert.
            # Non-unique integrity failures also become the explicit reload
            # failure below instead of poisoning the caller's transaction.
            pass

    existing = session.scalar(select(model).filter_by(**lookup))
    if existing is None:
        raise RuntimeError(
            f"Unable to create or reload {model.__name__} using {lookup!r}"
        )
    return existing


def find_or_create_group(
    session: Session,
    *,
    identifier: str,
    name: str | None = None,
) -> GroupModel:
    existing = session.scalar(
        select(GroupModel).where(GroupModel.identifier == identifier)
    )
    if existing is not None:
        return existing

    authorization_key = f"authorization:{identifier}"
    return _get_or_create(
        session,
        GroupModel,
        lookup={"authorization_key": authorization_key},
        values={
            "name": name or identifier,
            "identifier": identifier,
            "authorization_key": authorization_key,
        },
        conflict_columns=("authorization_key",),
    )


def add_user_to_group(
    session: Session,
    *,
    user_id: int,
    group_identifier: str,
    group_name: str | None = None,
) -> UserGroupAssignmentModel:
    group = find_or_create_group(
        session,
        identifier=group_identifier,
        name=group_name,
    )
    return _get_or_create(
        session,
        UserGroupAssignmentModel,
        lookup={"user_id": user_id, "group_id": group.id},
        values={"user_id": user_id, "group_id": group.id},
        conflict_columns=("user_id", "group_id"),
    )


def find_or_create_principal_for_user(
    session: Session,
    *,
    user_id: int,
) -> PrincipalModel:
    return _get_or_create(
        session,
        PrincipalModel,
        lookup={"user_id": user_id},
        values={"user_id": user_id},
        conflict_columns=("user_id",),
    )


def find_or_create_principal_for_group(
    session: Session,
    *,
    group_id: int,
) -> PrincipalModel:
    return _get_or_create(
        session,
        PrincipalModel,
        lookup={"group_id": group_id},
        values={"group_id": group_id},
        conflict_columns=("group_id",),
    )


def find_or_create_permission_target(
    session: Session,
    *,
    command: str | None = None,
    resource_type: str,
    resource_id: str | int | None,
) -> PermissionTargetModel:
    normalized_resource_type = PermissionResourceType(resource_type.strip()).value
    normalized_resource_id = (
        str(resource_id).strip() if resource_id is not None else None
    )
    if not normalized_resource_id and resource_id is not None:
        raise InvalidPermissionTargetError("resource_id cannot be blank")
    candidate = PermissionTargetModel(
        command=command,
        resource_type=normalized_resource_type,
        resource_id=normalized_resource_id,
    )
    lookup = {
        "resource_type": candidate.resource_type,
        "resource_id": candidate.resource_id,
        "command": candidate.command,
    }
    conflict_columns = (
        ("resource_type", "resource_id", "command")
        if candidate.command is not None
        else ()
    )
    return _get_or_create(
        session,
        PermissionTargetModel,
        lookup=lookup,
        values={
            "command": candidate.command,
            "resource_type": candidate.resource_type,
            "resource_id": candidate.resource_id,
        },
        conflict_columns=conflict_columns,
    )


def grant_permission_to_group(
    session: Session,
    *,
    group_identifier: str,
    permission: str,
    command: str | None = None,
    resource_type: str,
    resource_id: str | int | None,
    grant_type: str = PermitDeny.permit.value,
    group_name: str | None = None,
) -> PermissionAssignmentModel:
    group = find_or_create_group(
        session,
        identifier=group_identifier,
        name=group_name,
    )
    principal = find_or_create_principal_for_group(session, group_id=group.id)
    permission_target = find_or_create_permission_target(
        session,
        command=command,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return _find_or_create_permission_assignment(
        session,
        principal_id=principal.id,
        permission_target_id=permission_target.id,
        permission=permission,
        grant_type=grant_type,
    )


def grant_permission_to_user(
    session: Session,
    *,
    user_id: int,
    permission: str,
    command: str | None = None,
    resource_type: str,
    resource_id: str | int | None,
    grant_type: str = PermitDeny.permit.value,
) -> PermissionAssignmentModel:
    principal = find_or_create_principal_for_user(session, user_id=user_id)
    permission_target = find_or_create_permission_target(
        session,
        command=command,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return _find_or_create_permission_assignment(
        session,
        principal_id=principal.id,
        permission_target_id=permission_target.id,
        permission=permission,
        grant_type=grant_type,
    )


def grant_command_permissions_to_group(
    session: Session,
    *,
    group_identifier: str,
    command_keys: Sequence[str],
    grant_type: str = PermitDeny.permit.value,
    group_name: str | None = None,
) -> list[PermissionAssignmentModel]:
    assignments: list[PermissionAssignmentModel] = []
    for command_key in command_keys:
        spec = authorization_spec_for_command_key(command_key)
        assignments.append(
            grant_permission_to_group(
                session,
                group_identifier=group_identifier,
                permission=spec.permission,
                command=spec.command_key,
                resource_type=RESOURCE_TENANT,
                resource_id=group_identifier.split(":", 1)[0],
                grant_type=grant_type,
                group_name=group_name,
            )
        )
    return assignments


def ensure_v1_role(
    session: Session,
    *,
    tenant_id: str,
    role_name: str,
    user_ids: Iterable[int] = (),
) -> GroupModel:
    if role_name not in BASIC_ROLE_NAMES:
        raise KeyError(f"Unknown V1 role: {role_name}")

    group_identifier = tenant_role_group_identifier(tenant_id, role_name)
    group = find_or_create_group(
        session,
        identifier=group_identifier,
        name=role_name,
    )
    for user_id in user_ids:
        add_user_to_group(
            session,
            user_id=user_id,
            group_identifier=group_identifier,
            group_name=role_name,
        )

    grant_command_permissions_to_group(
        session,
        group_identifier=group_identifier,
        command_keys=V1_BASIC_ROLE_COMMAND_KEYS[role_name],
        group_name=role_name,
    )
    session.flush()
    return group


def _find_or_create_permission_assignment(
    session: Session,
    *,
    principal_id: int,
    permission_target_id: int,
    permission: str,
    grant_type: str,
) -> PermissionAssignmentModel:
    assignment = _get_or_create(
        session,
        PermissionAssignmentModel,
        lookup={
            "principal_id": principal_id,
            "permission_target_id": permission_target_id,
            "permission": permission,
        },
        values={
            "principal_id": principal_id,
            "permission_target_id": permission_target_id,
            "permission": permission,
            "grant_type": grant_type,
        },
        conflict_columns=(
            "principal_id",
            "permission_target_id",
            "permission",
        ),
    )
    if assignment.grant_type != grant_type:
        assignment.grant_type = grant_type
        session.flush()
    return assignment


__all__ = [
    "AuthorizationDecision",
    "AuthorizationPolicy",
    "AuthorizationRequest",
    "AuthorizationPolicyFactory",
    "BASIC_ROLE_NAMES",
    "COMMAND_AUTHORIZATION_SPECS",
    "COMMAND_AUTHORIZATION_SPECS_BY_KEY",
    "CommandAuthorizationSpec",
    "DatabaseAuthorizationPolicy",
    "PROCESS_START_COMMAND",
    "ROLE_ADMIN",
    "ROLE_MANAGER",
    "ROLE_USER",
    "TASK_CLAIM_COMMAND",
    "TASK_COMPLETE_COMMAND",
    "V1_BASIC_ROLE_COMMAND_KEYS",
    "actor_user_id_from_command",
    "add_user_to_group",
    "authorization_policy_scope",
    "authorization_spec_for_command",
    "authorization_spec_for_command_key",
    "build_authorization_request",
    "ensure_v1_role",
    "find_or_create_group",
    "find_or_create_permission_target",
    "find_or_create_principal_for_group",
    "find_or_create_principal_for_user",
    "grant_command_permissions_to_group",
    "grant_permission_to_group",
    "grant_permission_to_user",
    "group_identifier_applies_to_tenant",
    "permission_assignment_matches_request",
    "permission_assignments_for_user",
    "require_command_authorization",
    "resolve_authorization_policy",
    "set_default_authorization_policy_factory",
    "tenant_role_group_identifier",
]
