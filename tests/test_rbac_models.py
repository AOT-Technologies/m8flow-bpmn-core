from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from m8flow_bpmn_core.models import (
    GroupModel,
    PermissionAction,
    PermissionAssignmentModel,
    PermissionTargetModel,
    PermitDeny,
    PrincipalModel,
    UserGroupAssignmentModel,
    UserModel,
)


def test_permission_targets_support_shared_uri_with_distinct_commands(
    session: Session,
) -> None:
    claim_target = PermissionTargetModel(
        uri="/tasks/*",
        command="task.claim",
    )
    complete_target = PermissionTargetModel(
        uri="/tasks/*",
        command="task.complete",
    )
    session.add_all([claim_target, complete_target])
    session.flush()

    assert claim_target.uri == "/tasks/%"
    assert complete_target.uri == "/tasks/%"
    assert claim_target.id != complete_target.id


def test_permission_target_rejects_duplicate_uri_command_pairs(
    session: Session,
) -> None:
    session.add(
        PermissionTargetModel(
            uri="/tasks/*",
            command="task.claim",
        )
    )
    session.flush()

    session.add(
        PermissionTargetModel(
            uri="/tasks/%",
            command="task.claim",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_user_group_principal_and_permission_assignment_link_up(
    session: Session,
) -> None:
    user = UserModel(
        username="alice",
        email="alice@example.com",
        service="http://localhost:7002/realms/tenant-a",
        service_id="alice-keycloak",
        display_name="Alice",
        created_at_in_seconds=1,
        updated_at_in_seconds=1,
    )
    group = GroupModel(
        name="Tenant A Manager",
        identifier="tenant-a:manager",
        source_is_open_id=True,
    )
    session.add_all([user, group])
    session.flush()

    session.add(
        UserGroupAssignmentModel(
            user_id=user.id,
            group_id=group.id,
        )
    )
    session.add_all(
        [
            PrincipalModel(user_id=user.id),
            PrincipalModel(group_id=group.id),
        ]
    )
    session.flush()

    permission_target = PermissionTargetModel(
        uri="/tasks/*",
        command="task.complete",
    )
    session.add(permission_target)
    session.flush()

    session.add(
        PermissionAssignmentModel(
            principal_id=group.principal.id,
            permission_target_id=permission_target.id,
            permission=PermissionAction.execute.value,
            grant_type=PermitDeny.permit.value,
        )
    )
    session.flush()
    session.refresh(user)
    session.refresh(group)

    assert [assigned_group.identifier for assigned_group in user.groups] == [
        "tenant-a:manager"
    ]
    assert user.principal is not None
    assert group.principal is not None
    assert group.principal.permission_assignments[0].permission_target.command == (
        "task.complete"
    )
