from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class WorkItemUserAddedBy(StrEnum):
    guest = "guest"
    lane_assignment = "lane_assignment"
    lane_owner = "lane_owner"
    manual = "manual"
    process_initiator = "process_initiator"


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
    def validate_added_by(self, key: str, value: Any) -> Any:
        if value is None:
            return None
        return WorkItemUserAddedBy(value).value
