from __future__ import annotations

import re

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base


class InvalidPermissionTargetUriError(ValueError):
    pass


class PermissionTargetModel(Base):
    URI_ALL = "/%"

    __tablename__ = "permission_target"
    __table_args__ = (
        UniqueConstraint(
            "uri",
            "command",
            name="permission_target_uri_command_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uri: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    command: Mapped[str | None] = mapped_column(String(255), index=True)

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
