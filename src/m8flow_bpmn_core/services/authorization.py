from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import or_, select
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
from m8flow_bpmn_core.models.permission_target import PermissionTargetModel
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

TASKS_TARGET_URI = "/tasks/%"
PROCESS_DEFINITIONS_TARGET_URI = "/process-definitions/%"
PROCESS_INSTANCES_TARGET_URI = "/process-instances/%"
PROCESS_MODELS_TARGET_URI = "/process-models/%"

ROLE_USER = "user"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"
BASIC_ROLE_NAMES = frozenset({ROLE_USER, ROLE_MANAGER, ROLE_ADMIN})


@dataclass(frozen=True, slots=True)
class CommandAuthorizationSpec:
    command_key: str
    permission: str
    target_uri: str
    actor_field_name: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    tenant_id: str
    actor_user_id: int
    command_key: str
    permission: str
    target_uri: str
    target_id: int | None = None
    metadata: Mapping[str, object] | None = None


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
        target_uri=TASKS_TARGET_URI,
        actor_field_name="user_id",
    ),
    CompleteTaskCommand: CommandAuthorizationSpec(
        command_key=TASK_COMPLETE_COMMAND,
        permission=PermissionAction.execute.value,
        target_uri=TASKS_TARGET_URI,
        actor_field_name="user_id",
    ),
    CreateProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_CREATE_COMMAND,
        permission=PermissionAction.create.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
        actor_field_name="process_initiator_id",
    ),
    ImportBpmnProcessDefinitionCommand: CommandAuthorizationSpec(
        command_key=PROCESS_DEFINITION_IMPORT_COMMAND,
        permission=PermissionAction.create.value,
        target_uri=PROCESS_DEFINITIONS_TARGET_URI,
    ),
    InitializeProcessInstanceFromDefinitionCommand: CommandAuthorizationSpec(
        command_key=PROCESS_START_COMMAND,
        permission=PermissionAction.start.value,
        target_uri=PROCESS_MODELS_TARGET_URI,
        actor_field_name="process_initiator_id",
    ),
    InitializeProcessInstanceWorkflowCommand: CommandAuthorizationSpec(
        command_key=PROCESS_WORKFLOW_INITIALIZE_COMMAND,
        permission=PermissionAction.execute.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
    ),
    UpsertProcessInstanceMetadataCommand: CommandAuthorizationSpec(
        command_key=PROCESS_METADATA_UPSERT_COMMAND,
        permission=PermissionAction.update.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
    ),
    RecordProcessInstanceEventCommand: CommandAuthorizationSpec(
        command_key=PROCESS_EVENT_RECORD_COMMAND,
        permission=PermissionAction.create.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
        actor_field_name="user_id",
    ),
    SuspendProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_SUSPEND_COMMAND,
        permission=PermissionAction.execute.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
        actor_field_name="user_id",
    ),
    ResumeProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_RESUME_COMMAND,
        permission=PermissionAction.execute.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
        actor_field_name="user_id",
    ),
    ErrorProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_ERROR_COMMAND,
        permission=PermissionAction.execute.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
        actor_field_name="user_id",
    ),
    RetryProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_RETRY_COMMAND,
        permission=PermissionAction.execute.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
        actor_field_name="user_id",
    ),
    TerminateProcessInstanceCommand: CommandAuthorizationSpec(
        command_key=PROCESS_TERMINATE_COMMAND,
        permission=PermissionAction.execute.value,
        target_uri=PROCESS_INSTANCES_TARGET_URI,
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
                    f"{request.command_key} on {request.target_uri}"
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
                f"{request.command_key} on {request.target_uri}"
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
    target_uri: str | None = None,
    target_id: int | None = None,
    metadata: Mapping[str, object] | None = None,
) -> AuthorizationRequest:
    spec = authorization_spec_for_command_key(command_key)
    return AuthorizationRequest(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        command_key=command_key,
        permission=permission or spec.permission,
        target_uri=target_uri or spec.target_uri,
        target_id=target_id,
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
    target_uri: str | None = None,
    target_id: int | None = None,
    metadata: Mapping[str, object] | None = None,
    policy: AuthorizationPolicy | None = None,
) -> None:
    request = build_authorization_request(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        command_key=command_key,
        permission=permission,
        target_uri=target_uri,
        target_id=target_id,
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

    return permission_target_matches_uri(permission_target, request.target_uri)


def permission_target_matches_uri(
    permission_target: PermissionTargetModel,
    target_uri: str,
) -> bool:
    normalized_target_uri = target_uri.strip()
    normalized_permission_uri = permission_target.uri.strip()

    if normalized_permission_uri.endswith("%"):
        return normalized_target_uri.startswith(normalized_permission_uri[:-1])
    return normalized_permission_uri == normalized_target_uri


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


def find_or_create_group(
    session: Session,
    *,
    identifier: str,
    name: str | None = None,
    source_is_open_id: bool = False,
) -> GroupModel:
    group = session.scalar(
        select(GroupModel).where(GroupModel.identifier == identifier)
    )
    if group is None:
        group = GroupModel(
            name=name or identifier,
            identifier=identifier,
            source_is_open_id=source_is_open_id,
        )
        session.add(group)
        session.flush()
    return group


def add_user_to_group(
    session: Session,
    *,
    user_id: int,
    group_identifier: str,
    group_name: str | None = None,
    source_is_open_id: bool = False,
) -> UserGroupAssignmentModel:
    group = find_or_create_group(
        session,
        identifier=group_identifier,
        name=group_name,
        source_is_open_id=source_is_open_id,
    )
    assignment = session.scalar(
        select(UserGroupAssignmentModel).where(
            UserGroupAssignmentModel.user_id == user_id,
            UserGroupAssignmentModel.group_id == group.id,
        )
    )
    if assignment is None:
        assignment = UserGroupAssignmentModel(
            user_id=user_id,
            group_id=group.id,
        )
        session.add(assignment)
        session.flush()
    return assignment


def find_or_create_principal_for_user(
    session: Session,
    *,
    user_id: int,
) -> PrincipalModel:
    principal = session.scalar(
        select(PrincipalModel).where(PrincipalModel.user_id == user_id)
    )
    if principal is None:
        principal = PrincipalModel(user_id=user_id)
        session.add(principal)
        session.flush()
    return principal


def find_or_create_principal_for_group(
    session: Session,
    *,
    group_id: int,
) -> PrincipalModel:
    principal = session.scalar(
        select(PrincipalModel).where(PrincipalModel.group_id == group_id)
    )
    if principal is None:
        principal = PrincipalModel(group_id=group_id)
        session.add(principal)
        session.flush()
    return principal


def find_or_create_permission_target(
    session: Session,
    *,
    uri: str,
    command: str | None = None,
) -> PermissionTargetModel:
    permission_target = session.scalar(
        select(PermissionTargetModel).where(
            PermissionTargetModel.uri == uri.replace("*", "%"),
            PermissionTargetModel.command == command,
        )
    )
    if permission_target is None:
        permission_target = PermissionTargetModel(uri=uri, command=command)
        session.add(permission_target)
        session.flush()
    return permission_target


def grant_permission_to_group(
    session: Session,
    *,
    group_identifier: str,
    permission: str,
    target_uri: str,
    command: str | None = None,
    grant_type: str = PermitDeny.permit.value,
    group_name: str | None = None,
    source_is_open_id: bool = False,
) -> PermissionAssignmentModel:
    group = find_or_create_group(
        session,
        identifier=group_identifier,
        name=group_name,
        source_is_open_id=source_is_open_id,
    )
    principal = find_or_create_principal_for_group(session, group_id=group.id)
    permission_target = find_or_create_permission_target(
        session,
        uri=target_uri,
        command=command,
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
    target_uri: str,
    command: str | None = None,
    grant_type: str = PermitDeny.permit.value,
) -> PermissionAssignmentModel:
    principal = find_or_create_principal_for_user(session, user_id=user_id)
    permission_target = find_or_create_permission_target(
        session,
        uri=target_uri,
        command=command,
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
    source_is_open_id: bool = False,
) -> list[PermissionAssignmentModel]:
    assignments: list[PermissionAssignmentModel] = []
    for command_key in command_keys:
        spec = authorization_spec_for_command_key(command_key)
        assignments.append(
            grant_permission_to_group(
                session,
                group_identifier=group_identifier,
                permission=spec.permission,
                target_uri=spec.target_uri,
                command=spec.command_key,
                grant_type=grant_type,
                group_name=group_name,
                source_is_open_id=source_is_open_id,
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
    assignment = session.scalar(
        select(PermissionAssignmentModel).where(
            PermissionAssignmentModel.principal_id == principal_id,
            PermissionAssignmentModel.permission_target_id == permission_target_id,
            PermissionAssignmentModel.permission == permission,
        )
    )
    if assignment is None:
        assignment = PermissionAssignmentModel(
            principal_id=principal_id,
            permission_target_id=permission_target_id,
            permission=permission,
            grant_type=grant_type,
        )
        session.add(assignment)
        session.flush()
        return assignment

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
    "permission_target_matches_uri",
    "require_command_authorization",
    "resolve_authorization_policy",
    "set_default_authorization_policy_factory",
    "tenant_role_group_identifier",
]
