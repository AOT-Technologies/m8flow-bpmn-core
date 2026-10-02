from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class WorkItemModel(M8fTenantScopedMixin, TenantScoped, Base):
    """Canonical claim state for a human task.

    The primary key is intentionally the legacy human-task id so existing
    consumers can migrate without changing task identifiers. Display and form
    metadata remains owned by HumanTaskModel and related workflow models.
    """

    __tablename__ = "work_item"
    id: Mapped[int] = mapped_column(
        ForeignKey("human_task.id", ondelete="CASCADE"), primary_key=True
    )
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"), index=True, nullable=False
    )
    task_guid: Mapped[str | None] = mapped_column(
        ForeignKey("task.guid"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(50))
    lane_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("group.id"), index=True
    )
    completed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), index=True
    )
    actual_owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), index=True
    )
    task_status: Mapped[str] = mapped_column(String(50), nullable=False)
    completed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    human_task = relationship("HumanTaskModel", back_populates="work_item")
    process_instance = relationship("ProcessInstanceModel")
    task_model = relationship("TaskModel")
    lane_assignment = relationship("GroupModel")
    completed_by_user = relationship("UserModel", foreign_keys=[completed_by_user_id])
    actual_owner = relationship("UserModel", foreign_keys=[actual_owner_id])
