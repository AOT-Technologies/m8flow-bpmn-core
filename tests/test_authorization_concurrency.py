from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import func, select

from m8flow_bpmn_core.db import build_engine, build_session_factory, create_schema
from m8flow_bpmn_core.models import Base
from m8flow_bpmn_core.models.group import GroupModel
from m8flow_bpmn_core.models.permission_assignment import PermissionAssignmentModel
from m8flow_bpmn_core.models.permission_target import PermissionTargetModel
from m8flow_bpmn_core.models.principal import PrincipalModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.models.user_group_assignment import UserGroupAssignmentModel
from m8flow_bpmn_core.services.authorization import (
    ROLE_MANAGER,
    TASK_CLAIM_COMMAND,
    ensure_v1_role,
    grant_permission_to_user,
)


def _initialize_authorization_state(
    database_url: str,
    *,
    user_id: int,
) -> tuple[int, int, int, int, int, int, int]:
    engine = build_engine(database_url)
    session_factory = build_session_factory(engine)
    try:
        with session_factory() as session:
            group = ensure_v1_role(
                session,
                tenant_id="tenant-concurrent",
                role_name=ROLE_MANAGER,
                user_ids=[user_id],
            )
            permission_assignment = grant_permission_to_user(
                session,
                user_id=user_id,
                permission="execute",
                target_uri="/tasks/123",
                command=TASK_CLAIM_COMMAND,
                resource_type="task",
                resource_id=123,
            )
            legacy_permission_assignment = grant_permission_to_user(
                session,
                user_id=user_id,
                permission="execute",
                target_uri="/ui/tasks/123",
                command=None,
            )
            session.commit()
            return (
                group.id,
                group.principal.id,
                permission_assignment.permission_target_id,
                permission_assignment.id,
                group.user_group_assignments[0].id,
                legacy_permission_assignment.permission_target_id,
                legacy_permission_assignment.id,
            )
    finally:
        engine.dispose()


def test_authorization_initialization_is_race_safe(tmp_path: Path) -> None:
    database_path = tmp_path / "authorization-concurrency.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    schema_engine = build_engine(database_url)
    create_schema(schema_engine)
    schema_engine.dispose()

    setup_engine = build_engine(database_url)
    session_factory = build_session_factory(setup_engine)
    try:
        with session_factory() as session:
            user = UserModel(
                username="concurrent-user",
                email="concurrent-user@example.com",
                service="http://localhost:7002/realms/tenant-concurrent",
                service_id="concurrent-user-keycloak",
                display_name="Concurrent User",
                created_at_in_seconds=1,
                updated_at_in_seconds=1,
            )
            session.add(user)
            session.commit()
            user_id = user.id
    finally:
        setup_engine.dispose()

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _worker: _initialize_authorization_state(
                        database_url,
                        user_id=user_id,
                    ),
                    range(8),
                )
            )

        verification_engine = build_engine(database_url)
        verification_factory = build_session_factory(verification_engine)
        try:
            with verification_factory() as session:
                (
                    group_id,
                    principal_id,
                    target_id,
                    assignment_id,
                    membership_id,
                    legacy_target_id,
                    legacy_assignment_id,
                ) = results[0]
                assert {result[0] for result in results} == {group_id}
                assert {result[1] for result in results} == {principal_id}
                assert {result[2] for result in results} == {target_id}
                assert {result[3] for result in results} == {assignment_id}
                assert {result[4] for result in results} == {membership_id}
                assert {result[5] for result in results} == {legacy_target_id}
                assert {result[6] for result in results} == {legacy_assignment_id}

                assert session.scalar(select(func.count()).select_from(GroupModel)) == 1
                assert (
                    session.scalar(select(func.count()).select_from(PrincipalModel))
                    == 2
                )
                assert (
                    session.scalar(
                        select(func.count()).select_from(UserGroupAssignmentModel)
                    )
                    == 1
                )
                assert (
                    session.scalar(
                        select(func.count()).select_from(PermissionTargetModel)
                    )
                    == 4
                )
                assert (
                    session.scalar(
                        select(func.count()).select_from(PermissionAssignmentModel)
                    )
                    == 4
                )
        finally:
            verification_engine.dispose()
    finally:
        cleanup_engine = build_engine(database_url)
        Base.metadata.drop_all(bind=cleanup_engine)
        cleanup_engine.dispose()
