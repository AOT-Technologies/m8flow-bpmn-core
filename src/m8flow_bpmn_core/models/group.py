from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base


class GroupModel(Base):
    __tablename__ = "m8f_group"
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
