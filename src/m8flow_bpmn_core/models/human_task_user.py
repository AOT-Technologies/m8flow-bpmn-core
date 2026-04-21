from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class HumanTaskUserAddedBy(StrEnum):
    guest = "guest"
    lane_assignment = "lane_assignment"
    lane_owner = "lane_owner"
    manual = "manual"
    process_initiator = "process_initiator"


class HumanTaskUserModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "human_task_user"
    __table_args__ = (
        UniqueConstraint("human_task_id", "user_id", name="human_task_user_unique"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    human_task_id: Mapped[int] = mapped_column(
        ForeignKey("human_task.id"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"), index=True, nullable=False
    )
    added_by: Mapped[str | None] = mapped_column(String(20), index=True)

    human_task = relationship("HumanTaskModel", back_populates="human_task_users")
    user = relationship("UserModel")

    @validates("added_by")
    def validate_added_by(self, key: str, value: Any) -> Any:
        if value is None:
            return None
        return HumanTaskUserAddedBy(value).value
