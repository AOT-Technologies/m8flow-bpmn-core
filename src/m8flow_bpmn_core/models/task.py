from __future__ import annotations

from datetime import datetime
from typing import Any

from SpiffWorkflow.util.task import TaskState
from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped

# SpiffWorkflow has no TERMINATED execution state.  M8Flow historically uses
# this compatibility value when a process-level operation closes a task.
M8F_TERMINATED_TASK_STATE = "TERMINATED"


class TaskModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "task"
    guid: Mapped[str] = mapped_column(String(36), primary_key=True)
    bpmn_process_id: Mapped[int] = mapped_column(
        ForeignKey("bpmn_process.id"),
        index=True,
        nullable=False,
    )
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"),
        index=True,
        nullable=False,
    )
    task_definition_id: Mapped[int] = mapped_column(
        ForeignKey("task_definition.id"),
        index=True,
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    json_data_hash: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    python_env_data_hash: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    runtime_info: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    bpmn_process = relationship("BpmnProcessModel", back_populates="tasks")
    process_instance = relationship("ProcessInstanceModel", back_populates="tasks")
    task_definition = relationship("TaskDefinitionModel")
    work_items = relationship("WorkItemModel", back_populates="task_model")
    future_task = relationship(
        "FutureTaskModel",
        back_populates="task_model",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )

    @validates("state")
    def validate_state(self, key: str, value: Any) -> str:
        from m8flow_bpmn_core.errors import ValidationError

        if value == M8F_TERMINATED_TASK_STATE:
            return M8F_TERMINATED_TASK_STATE
        try:
            state_value = (
                value
                if isinstance(value, int)
                else TaskState.get_value(value)
            )
            if state_value not in TaskState._values:
                raise ValueError(f"Unknown TaskState value: {state_value}")
            normalized = TaskState.get_name(state_value)
            if not normalized:
                raise ValueError(f"Unknown TaskState value: {state_value}")
            return str(normalized)
        except (KeyError, TypeError, ValueError) as exc:
            allowed_values = ", ".join(TaskState._names)
            raise ValidationError(
                f"Invalid task state: {value!r}. Expected one of: "
                f"{allowed_values}, {M8F_TERMINATED_TASK_STATE}"
            ) from exc
