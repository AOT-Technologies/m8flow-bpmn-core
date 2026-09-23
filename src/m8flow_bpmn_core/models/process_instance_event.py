from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String
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


class ProcessLifecycleEventType(StrEnum):
    process_instance_created = ProcessInstanceEventType.process_instance_created
    process_instance_completed = ProcessInstanceEventType.process_instance_completed
    process_instance_error = ProcessInstanceEventType.process_instance_error
    process_instance_force_run = ProcessInstanceEventType.process_instance_force_run
    process_instance_migrated = ProcessInstanceEventType.process_instance_migrated
    process_instance_resumed = ProcessInstanceEventType.process_instance_resumed
    process_instance_retried = ProcessInstanceEventType.process_instance_retried
    process_instance_rewound_to_task = (
        ProcessInstanceEventType.process_instance_rewound_to_task
    )
    process_instance_suspended = ProcessInstanceEventType.process_instance_suspended
    process_instance_suspended_for_error = (
        ProcessInstanceEventType.process_instance_suspended_for_error
    )
    process_instance_terminated = ProcessInstanceEventType.process_instance_terminated


class TaskEventType(StrEnum):
    task_cancelled = ProcessInstanceEventType.task_cancelled
    task_completed = ProcessInstanceEventType.task_completed
    task_data_edited = ProcessInstanceEventType.task_data_edited
    task_executed_manually = ProcessInstanceEventType.task_executed_manually
    task_failed = ProcessInstanceEventType.task_failed
    task_skipped = ProcessInstanceEventType.task_skipped


class ProcessInstanceEventCategory(StrEnum):
    process = "process"
    task = "task"


class ProcessInstanceEventModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "process_instance_event"
    __timestamp_compatibility_pairs__ = (("timestamp", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_guid: Mapped[str | None] = mapped_column(String(36), index=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(20), index=True)
    timestamp: Mapped[float] = mapped_column(
        Numeric(17, 6),
        index=True,
        nullable=False,
    )
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)

    process_instance = relationship(
        "ProcessInstanceModel",
        back_populates="process_instance_events",
    )
    user = relationship("UserModel")

    @validates("event_type")
    def validate_event_type(self, key: str, value: Any) -> Any:
        from m8flow_bpmn_core.errors import ValidationError

        try:
            normalized = ProcessInstanceEventType(value).value
            self.category = (
                ProcessInstanceEventCategory.task.value
                if normalized.startswith("task_")
                else ProcessInstanceEventCategory.process.value
            )
            return normalized
        except ValueError as exc:  # pragma: no cover - defensive guard
            allowed_values = ", ".join(
                event_type.value for event_type in ProcessInstanceEventType
            )
            raise ValidationError(
                f"Invalid process instance event type: {value!r}. "
                f"Expected one of: {allowed_values}"
            ) from exc
