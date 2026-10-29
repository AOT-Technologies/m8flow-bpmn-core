from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_core.models.base import Base
from m8flow_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class WorkItemUserAddedBy(StrEnum):
    """Why a user is a potential owner of a work item.

    ``process_initiator``: the user started the instance and the task has no
    lane or sits in one of the definition's ``initiator_lanes``.
    ``lane_owner``: the user is named for the task's lane in ``lane_owners``.
    ``lane_assignment``: the user is a member of the task's lane group.
    """

    process_initiator = "process_initiator"
    lane_owner = "lane_owner"
    lane_assignment = "lane_assignment"


class WorkItemUserModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "work_item_user"
    __table_args__ = (
        UniqueConstraint("work_item_id", "user_id", name="m8f_work_item_user_key"),
    )

    work_item_id: Mapped[int] = mapped_column(
        ForeignKey("work_item.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"), primary_key=True, index=True, nullable=False
    )
    added_by: Mapped[str | None] = mapped_column(String(20), index=True)

    work_item = relationship("WorkItemModel", back_populates="work_item_users")
    user = relationship("UserModel")

    @validates("added_by")
    def validate_added_by(self, key: str, value: Any) -> str | None:
        """Accept one of the three labels or ``None`` on write.

        Values already stored in the column are loaded as-is and never pass
        through this check, so rows carrying older labels stay readable.
        """
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return WorkItemUserAddedBy(value).value
            except ValueError:
                pass
        from m8flow_core.errors import ValidationError

        allowed = ", ".join(member.value for member in WorkItemUserAddedBy)
        raise ValidationError(
            f"Invalid work item user added_by {value!r}; expected one of "
            f"{allowed}, or None"
        )
