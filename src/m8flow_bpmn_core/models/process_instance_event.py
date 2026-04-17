from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class ProcessInstanceEventType(StrEnum):
    process_instance_created = "process_instance_created"
    process_instance_completed = "process_instance_completed"
    process_instance_error = "process_instance_error"
    process_instance_force_run = "process_instance_force_run"
    process_instance_migrated = "process_instance_migrated"
    process_instance_resumed = "process_instance_resumed"
    process_instance_retried = "process_instance_retried"
    process_instance_rewound_to_task = "process_instance_rewound_to_task"
    process_instance_suspended = "process_instance_suspended"
    process_instance_suspended_for_error = "process_instance_suspended_for_error"
    process_instance_terminated = "process_instance_terminated"
    task_cancelled = "task_cancelled"
    task_completed = "task_completed"
    task_data_edited = "task_data_edited"
    task_executed_manually = "task_executed_manually"
    task_failed = "task_failed"
    task_skipped = "task_skipped"


class ProcessInstanceEventModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "process_instance_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_guid: Mapped[str | None] = mapped_column(String(36), index=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    timestamp: Mapped[float] = mapped_column(
        Numeric(17, 6),
        index=True,
        nullable=False,
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)

    process_instance = relationship(
        "ProcessInstanceModel",
        back_populates="process_instance_events",
    )
    user = relationship("UserModel")

    @validates("event_type")
    def validate_event_type(self, key: str, value: Any) -> Any:
        try:
            return ProcessInstanceEventType(value).value
        except ValueError as exc:  # pragma: no cover - defensive guard
            allowed_values = ", ".join(
                event_type.value for event_type in ProcessInstanceEventType
            )
            raise ValueError(
                f"Invalid process instance event type: {value!r}. "
                f"Expected one of: {allowed_values}"
            ) from exc
