from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_core.models.base import Base
from m8flow_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class ProcessInstanceEventCategory(StrEnum):
    """Whether an event concerns the whole instance or a single task."""

    process = "process"
    task = "task"


class ProcessLifecycleEventType(StrEnum):
    """Events that describe the process instance as a whole.

    Member order is not part of the public contract.
    """

    process_instance_created = "process_instance_created"
    process_instance_suspended = "process_instance_suspended"
    process_instance_resumed = "process_instance_resumed"
    process_instance_error = "process_instance_error"
    process_instance_retried = "process_instance_retried"
    process_instance_completed = "process_instance_completed"
    process_instance_terminated = "process_instance_terminated"


class TaskEventType(StrEnum):
    """Outcomes of a single task; rows of these types carry a ``task_guid``.

    Member order is not part of the public contract.
    """

    task_completed = "task_completed"
    task_failed = "task_failed"
    task_cancelled = "task_cancelled"


_CATEGORY_BY_EVENT_TYPE: dict[str, ProcessInstanceEventCategory] = {
    **{
        member.value: ProcessInstanceEventCategory.process
        for member in ProcessLifecycleEventType
    },
    **{member.value: ProcessInstanceEventCategory.task for member in TaskEventType},
}


def _accepted_event_types() -> str:
    return ", ".join(sorted(_CATEGORY_BY_EVENT_TYPE))


def event_category_for_type(event_type: object) -> ProcessInstanceEventCategory:
    """Return the category of a recordable event type.

    Accepts an enum member or its exact string value. Matching is exact and
    case-sensitive. Anything else raises ``ValueError``.
    """
    key = event_type.value if isinstance(event_type, StrEnum) else event_type
    category = _CATEGORY_BY_EVENT_TYPE.get(key) if isinstance(key, str) else None
    if category is None:
        raise ValueError(
            f"Unknown process instance event type: {event_type!r}. "
            f"Expected one of: {_accepted_event_types()}"
        )
    return category


class ProcessInstanceEventModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "process_instance_event"
    __table_args__ = (
        CheckConstraint(
            "category IN ('process', 'task')",
            name="m8f_process_instance_event_category_check",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_guid: Mapped[str | None] = mapped_column(String(36), index=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)

    process_instance = relationship(
        "ProcessInstanceModel",
        back_populates="process_instance_events",
    )
    user = relationship("UserModel")

    @validates("event_type")
    def validate_event_type(self, key: str, value: Any) -> str:
        from m8flow_core.errors import ValidationError

        try:
            category = event_category_for_type(value)
        except ValueError as exc:
            raise ValidationError(
                f"Invalid process instance event type: {value!r}. "
                f"Expected one of: {_accepted_event_types()}"
            ) from exc
        self.category = category.value
        return str(value.value if isinstance(value, StrEnum) else value)

    @validates("category")
    def validate_category(self, key: str, value: Any) -> str:
        from m8flow_core.errors import ValidationError

        try:
            category = ProcessInstanceEventCategory(value)
        except ValueError as exc:
            raise ValidationError(
                f"Invalid process instance event category: {value!r}. "
                "Expected 'process' or 'task'."
            ) from exc

        current_type = self.__dict__.get("event_type")
        if current_type is None:
            return category.value
        try:
            expected = event_category_for_type(current_type)
        except ValueError as exc:
            raise ValidationError(
                f"Cannot assign category {category.value!r} to an event of "
                f"unrecognised type {current_type!r}."
            ) from exc
        if category is not expected:
            raise ValidationError(
                f"Event category {category.value!r} does not match event type "
                f"{current_type!r}; expected {expected.value!r}."
            )
        return category.value
