from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class HumanTaskModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "human_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"),
        index=True,
        nullable=False,
    )
    task_guid: Mapped[str | None] = mapped_column(ForeignKey("task.guid"), index=True)
    lane_assignment_id: Mapped[int | None] = mapped_column(index=True)
    completed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), index=True
    )
    actual_owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), index=True
    )
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_title: Mapped[str | None] = mapped_column(String(255))
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    task_status: Mapped[str] = mapped_column(String(50), nullable=False)
    process_model_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bpmn_process_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    lane_name: Mapped[str | None] = mapped_column(String(255))
    json_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    completed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    process_instance = relationship(
        "ProcessInstanceModel", back_populates="human_tasks"
    )
    task_model = relationship("TaskModel", back_populates="human_tasks")
    human_task_users = relationship(
        "HumanTaskUserModel", back_populates="human_task", cascade="all, delete-orphan"
    )
