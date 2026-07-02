from __future__ import annotations

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base


class UserGroupAssignmentModel(Base):
    __tablename__ = "user_group_assignment"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "group_id",
            name="user_group_assignment_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("group.id"),
        nullable=False,
        index=True,
    )

    user = relationship(
        "UserModel",
        overlaps="groups,user_group_assignments,users",
    )
    group = relationship(
        "GroupModel",
        overlaps="groups,user_group_assignments,users",
    )
