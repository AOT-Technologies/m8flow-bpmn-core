from __future__ import annotations

from enum import StrEnum

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base


class InvalidPermissionTargetError(ValueError):
    pass


class PermissionResourceType(StrEnum):
    process_definition = "process_definition"
    process_instance = "process_instance"
    process_model = "process_model"
    task = "task"
    tenant = "tenant"


class PermissionTargetModel(Base):
    __tablename__ = "permission_target"
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "command",
            name="m8f_permission_target_resource_command_key",
        ),
        CheckConstraint(
            "resource_type IS NOT NULL AND "
            "(resource_id IS NULL OR length(trim(resource_id)) > 0)",
            name="m8f_permission_target_resource_pair_check",
        ),
        CheckConstraint(
            "resource_type IN ("
            "'process_definition', 'process_instance', 'process_model', "
            "'task', 'tenant')",
            name="m8f_permission_target_resource_type_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    command: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(
        String(100), index=True, nullable=False
    )
    resource_id: Mapped[str | None] = mapped_column(String(255), index=True)

    permission_assignments = relationship(
        "PermissionAssignmentModel",
        back_populates="permission_target",
        cascade="all, delete-orphan",
    )

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
        if key == "resource_type":
            try:
                return PermissionResourceType(normalized).value
            except ValueError as exc:
                allowed = ", ".join(item.value for item in PermissionResourceType)
                raise InvalidPermissionTargetError(
                    f"Unknown permission resource type {value!r}; expected one of: "
                    f"{allowed}"
                ) from exc
        return normalized or None
