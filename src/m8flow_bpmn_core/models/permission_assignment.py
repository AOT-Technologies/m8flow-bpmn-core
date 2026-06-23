from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base


class PermitDeny(StrEnum):
    permit = "permit"
    deny = "deny"


class PermissionAction(StrEnum):
    all = "all"
    create = "create"
    delete = "delete"
    execute = "execute"
    read = "read"
    start = "start"
    update = "update"


class PermissionAssignmentModel(Base):
    __tablename__ = "permission_assignment"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "permission_target_id",
            "permission",
            name="permission_assignment_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    principal_id: Mapped[int] = mapped_column(
        ForeignKey("principal.id"),
        nullable=False,
        index=True,
    )
    permission_target_id: Mapped[int] = mapped_column(
        ForeignKey("permission_target.id"),
        nullable=False,
        index=True,
    )
    grant_type: Mapped[str] = mapped_column(String(50), nullable=False)
    permission: Mapped[str] = mapped_column(String(50), nullable=False)

    principal = relationship(
        "PrincipalModel",
        back_populates="permission_assignments",
    )
    permission_target = relationship(
        "PermissionTargetModel",
        back_populates="permission_assignments",
    )

    @validates("grant_type")
    def validate_grant_type(self, key: str, value: str) -> Any:
        return PermitDeny(value).value

    @validates("permission")
    def validate_permission(self, key: str, value: str) -> Any:
        return PermissionAction(value).value
