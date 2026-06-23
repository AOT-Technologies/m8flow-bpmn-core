from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base


class PrincipalModel(Base):
    __tablename__ = "principal"
    __table_args__ = (
        CheckConstraint(
            (
                "(user_id IS NOT NULL AND group_id IS NULL) OR "
                "(user_id IS NULL AND group_id IS NOT NULL)"
            ),
            name="principal_exactly_one_subject",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"),
        nullable=True,
        unique=True,
        index=True,
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("group.id"),
        nullable=True,
        unique=True,
        index=True,
    )

    user = relationship(
        "UserModel",
        viewonly=True,
        overlaps="principal",
    )
    group = relationship(
        "GroupModel",
        viewonly=True,
        overlaps="principal",
    )
    permission_assignments = relationship(
        "PermissionAssignmentModel",
        back_populates="principal",
        cascade="all, delete-orphan",
    )
