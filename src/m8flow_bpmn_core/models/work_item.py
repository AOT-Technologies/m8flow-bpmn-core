from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class WorkItemModel(M8fTenantScopedMixin, TenantScoped, Base):
    """Canonical claim state for one persisted workflow task."""

    __tablename__ = "work_item"
    __table_args__ = (
        UniqueConstraint("task_guid", name="m8f_work_item_task_guid_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"), index=True, nullable=False
    )
    task_guid: Mapped[str | None] = mapped_column(
        ForeignKey("task.guid"), index=True
    )
    lane_assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("m8f_group.id"), index=True
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

    process_instance = relationship(
        "ProcessInstanceModel", back_populates="work_items"
    )
    task_model = relationship(
        "TaskModel", foreign_keys=[task_guid], back_populates="work_items"
    )
    lane_assignment = relationship("GroupModel")
    completed_by_user = relationship("UserModel", foreign_keys=[completed_by_user_id])
    actual_owner = relationship("UserModel", foreign_keys=[actual_owner_id])
    potential_owners = relationship(
        "UserModel",
        secondary="work_item_user",
        viewonly=True,
        order_by="WorkItemUserModel.user_id",
    )
    work_item_users = relationship(
        "WorkItemUserModel",
        back_populates="work_item",
        cascade="all, delete-orphan",
    )

    @property
    def task_name(self) -> str | None:
        if self.task_model is None:
            return None
        value = self.task_model.task_definition.bpmn_identifier
        return value if isinstance(value, str) else None

    @property
    def task_title(self) -> str | None:
        if self.task_model is None:
            return None
        value = self.task_model.task_definition.bpmn_name
        return value if isinstance(value, str) else None

    @property
    def task_type(self) -> str | None:
        if self.task_model is None:
            return None
        value = self.task_model.task_definition.typename
        return value if isinstance(value, str) else None

    @property
    def process_model_display_name(self) -> str | None:
        if self.process_instance is None:
            return None
        value = self.process_instance.process_model_display_name
        return value if isinstance(value, str) else None

    @property
    def bpmn_process_identifier(self) -> str | None:
        return (
            self.process_instance.process_model_identifier
            if self.process_instance
            else None
        )

    @property
    def lane_name(self) -> str | None:
        if self.task_model is None:
            return None
        value = self.task_model.properties_json.get("lane")
        if value is None and self.task_model.runtime_info is not None:
            value = self.task_model.runtime_info.get("lane")
        return value if isinstance(value, str) else None

    @property
    def json_metadata(self) -> dict[str, object] | None:
        if self.task_model is None:
            return None
        metadata = dict(self.task_model.properties_json or {})
        process_definition = (
            self.process_instance.bpmn_process_definition
            if self.process_instance is not None
            else None
        )
        if process_definition is not None:
            lane_owners = process_definition.properties_json.get("lane_owners")
            if isinstance(lane_owners, dict):
                metadata["lane_owners"] = dict(lane_owners)
        return metadata
