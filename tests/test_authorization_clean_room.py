from __future__ import annotations

from pathlib import Path

from sqlalchemy import ForeignKeyConstraint, PrimaryKeyConstraint

from m8flow_bpmn_core.models.group import GroupModel
from m8flow_bpmn_core.models.permission_assignment import PermissionAssignmentModel
from m8flow_bpmn_core.models.permission_target import PermissionTargetModel
from m8flow_bpmn_core.models.principal import PrincipalModel
from m8flow_bpmn_core.models.user import UserModel
from m8flow_bpmn_core.models.user_group_assignment import UserGroupAssignmentModel

AUTHORIZATION_MODELS = (
    GroupModel,
    UserModel,
    UserGroupAssignmentModel,
    PrincipalModel,
    PermissionTargetModel,
    PermissionAssignmentModel,
)


def test_current_authorization_constraints_use_m8f_namespace() -> None:
    for model in AUTHORIZATION_MODELS:
        for constraint in model.__table__.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                continue
            assert constraint.name is not None
            assert constraint.name.startswith("m8f_") or isinstance(
                constraint, PrimaryKeyConstraint
            )


def test_authorization_relationships_use_current_explicit_topology() -> None:
    assert GroupModel.user_group_assignments.property.back_populates == "group"
    assert GroupModel.principal.property.back_populates == "group"
    assert UserModel.user_group_assignments.property.back_populates == "user"
    assert UserModel.principal.property.back_populates == "user"
    assert UserGroupAssignmentModel.user.property.back_populates == (
        "user_group_assignments"
    )
    assert UserGroupAssignmentModel.group.property.back_populates == (
        "user_group_assignments"
    )
    assert PrincipalModel.user.property.back_populates == "principal"
    assert PrincipalModel.group.property.back_populates == "principal"


def test_legacy_constraint_names_are_absent_from_current_authorization_source() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    source_paths = (
        repository_root / "src/m8flow_bpmn_core/services/authorization.py",
        repository_root / "src/m8flow_bpmn_core/models/group.py",
        repository_root / "src/m8flow_bpmn_core/models/user.py",
        repository_root / "src/m8flow_bpmn_core/models/user_group_assignment.py",
        repository_root / "src/m8flow_bpmn_core/models/principal.py",
        repository_root / "src/m8flow_bpmn_core/models/permission_target.py",
        repository_root / "src/m8flow_bpmn_core/models/permission_assignment.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    for legacy_name in (
        'name="principal_exactly_one_subject"',
        'name="permission_target_uri_command_unique"',
        'name="permission_assignment_unique"',
        'name="user_group_assignment_unique"',
    ):
        assert legacy_name not in source
