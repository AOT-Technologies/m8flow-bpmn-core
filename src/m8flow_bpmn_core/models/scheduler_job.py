from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import (
    M8fTenantScopedMixin,
    TenantScoped,
)


class SchedulerJobType(StrEnum):
    intermediate_timer = "intermediate_timer"
    process_retry = "process_retry"
    timer_start = "timer_start"


class SchedulerJobModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "scheduler_job"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "job_key",
            name="uq_scheduler_job_tenant_job_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_key: Mapped[str] = mapped_column(String(255), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    process_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("process_instance.id", ondelete="CASCADE"),
        index=True,
    )
    bpmn_process_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("bpmn_process_definition.id", ondelete="CASCADE"),
        index=True,
    )
    locked_by: Mapped[str | None] = mapped_column(String(255), index=True)
    locked_at_in_seconds: Mapped[int | None] = mapped_column(Integer, index=True)
    run_at_in_seconds: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    updated_at_in_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at_in_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    process_instance = relationship(
        "ProcessInstanceModel",
        back_populates="scheduler_jobs",
    )
    bpmn_process_definition = relationship(
        "BpmnProcessDefinitionModel",
        back_populates="scheduler_jobs",
    )

    @validates("job_type")
    def validate_job_type(self, key: str, value: Any) -> Any:
        from m8flow_bpmn_core.errors import ValidationError

        try:
            return SchedulerJobType(value).value
        except ValueError as exc:
            allowed_values = ", ".join(job_type.value for job_type in SchedulerJobType)
            raise ValidationError(
                f"Invalid scheduler job type: {value!r}. "
                f"Expected one of: {allowed_values}"
            ) from exc

    @property
    def is_locked(self) -> bool:
        return self.locked_by is not None
