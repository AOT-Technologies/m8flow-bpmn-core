from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import false

from m8flow_core.models.base import Base
from m8flow_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class FutureTaskModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "future_task"
    guid: Mapped[str] = mapped_column(
        ForeignKey(
            "task.guid", ondelete="CASCADE", name="m8f_future_task_task_guid_fk"
        ),
        primary_key=True,
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    queued_to_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    completed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    archived_for_process_instance_status: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    task_model = relationship("TaskModel", back_populates="future_task")
