from __future__ import annotations

import re

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint, and_, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base


class InvalidPermissionTargetUriError(ValueError):
    pass


class InvalidPermissionTargetError(ValueError):
    pass


class PermissionTargetModel(Base):
    URI_ALL = "/%"

    __tablename__ = "permission_target"
    __table_args__ = (
        UniqueConstraint(
            "uri",
            "command",
            name="m8f_permission_target_uri_command_key",
        ),
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "command",
            name="m8f_permission_target_resource_command_key",
        ),
        CheckConstraint(
            "(resource_type IS NULL AND resource_id IS NULL) OR "
            "(resource_type IS NOT NULL AND resource_id IS NOT NULL)",
            name="m8f_permission_target_resource_pair_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uri: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    command: Mapped[str | None] = mapped_column(String(255), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), index=True)

    permission_assignments = relationship(
        "PermissionAssignmentModel",
        back_populates="permission_target",
        cascade="all, delete-orphan",
    )

    @validates("uri")
    def validate_uri(self, key: str, value: str) -> str:
        normalized = re.sub(r"\*", "%", value.strip())
        if not normalized:
            raise InvalidPermissionTargetUriError(
                "Permission target uri cannot be blank"
            )
        if re.search(r"%.", normalized):
            raise InvalidPermissionTargetUriError(
                f"Wildcard must appear at end: {normalized}"
            )
        return normalized

    @validates("command")
    def validate_command(self, key: str, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @validates("resource_type", "resource_id")
    def validate_resource_component(self, key: str, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


Index(
    "m8f_permission_target_uri_command_identity_key",
    PermissionTargetModel.uri,
    func.coalesce(PermissionTargetModel.command, ""),
    unique=True,
)
Index(
    "m8f_permission_target_resource_command_identity_key",
    PermissionTargetModel.resource_type,
    PermissionTargetModel.resource_id,
    func.coalesce(PermissionTargetModel.command, ""),
    unique=True,
    sqlite_where=and_(
        PermissionTargetModel.resource_type.is_not(None),
        PermissionTargetModel.resource_id.is_not(None),
    ),
    postgresql_where=and_(
        PermissionTargetModel.resource_type.is_not(None),
        PermissionTargetModel.resource_id.is_not(None),
    ),
)
