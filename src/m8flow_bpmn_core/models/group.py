from __future__ import annotations

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import false

from m8flow_bpmn_core.models.base import Base


class GroupModel(Base):
    __tablename__ = "group"
    __table_args__ = (
        UniqueConstraint(
            "authorization_key",
            name="m8f_group_authorization_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)
    identifier: Mapped[str | None] = mapped_column(String(255), index=True)
    authorization_key: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
    )
    source_is_open_id: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
        index=True,
    )

    user_group_assignments = relationship(
        "UserGroupAssignmentModel",
        cascade="all, delete-orphan",
        back_populates="group",
    )
    users = relationship(
        "UserModel",
        viewonly=True,
        secondary="user_group_assignment",
        overlaps="user_group_assignments,user,group",
    )
    principal = relationship(
        "PrincipalModel",
        uselist=False,
        cascade="all, delete-orphan",
        back_populates="group",
    )
