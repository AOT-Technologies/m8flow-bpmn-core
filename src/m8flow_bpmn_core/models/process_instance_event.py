from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class ProcessInstanceEventType(StrEnum):
    """Compatibility enum containing the original combined event values."""

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


class TaskEventType(StrEnum):
    task_cancelled = "task_cancelled"
    task_completed = "task_completed"
    task_data_edited = "task_data_edited"
    task_executed_manually = "task_executed_manually"
    task_failed = "task_failed"
    task_skipped = "task_skipped"


class ProcessInstanceEventCategory(StrEnum):
    process = "process"
    task = "task"


def event_category_for_type(
    event_type: (
        ProcessInstanceEventType
        | ProcessLifecycleEventType
        | TaskEventType
        | str
    ),
) -> ProcessInstanceEventCategory:
    """Return the persisted category for any supported event enum/value."""
    value = event_type.value if isinstance(event_type, StrEnum) else event_type
    try:
        ProcessLifecycleEventType(value)
    except ValueError:
        try:
            TaskEventType(value)
        except ValueError as exc:
            raise ValueError(f"Unknown process instance event type: {value!r}") from exc
        return ProcessInstanceEventCategory.task
    return ProcessInstanceEventCategory.process


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
            normalized = (
                value.value
                if isinstance(value, StrEnum)
                else ProcessInstanceEventType(value).value
            )
            self.category = event_category_for_type(normalized).value
            return normalized
        except ValueError as exc:  # pragma: no cover - defensive guard
            allowed_values = ", ".join(
                event_type.value for event_type in ProcessInstanceEventType
            )
            raise ValidationError(
                f"Invalid process instance event type: {value!r}. "
                f"Expected one of: {allowed_values}"
            ) from exc
