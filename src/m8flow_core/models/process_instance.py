from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    object_session,
    relationship,
    validates,
)

from m8flow_core.models.base import Base
from m8flow_core.models.json_data import JsonDataModel
from m8flow_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped

WORKFLOW_STATE_JSON_DATA_KEY = "__m8f_workflow_state_json"


class ProcessInstanceStatus(StrEnum):
    """Lifecycle states stored in ``process_instance.status``."""

    not_started = "not_started"
    running = "running"
    user_input_required = "user_input_required"
    waiting = "waiting"
    suspended = "suspended"
    complete = "complete"
    error = "error"
    terminated = "terminated"


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        ProcessInstanceStatus.complete.value,
        ProcessInstanceStatus.error.value,
        ProcessInstanceStatus.terminated.value,
    }
)
_ACTIVE_STATUSES: tuple[str, ...] = (
    ProcessInstanceStatus.not_started.value,
    ProcessInstanceStatus.running.value,
    ProcessInstanceStatus.user_input_required.value,
    ProcessInstanceStatus.waiting.value,
)


class ProcessInstanceModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "process_instance"
    id: Mapped[int] = mapped_column(primary_key=True)
    process_model_identifier: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    process_model_display_name: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    summary: Mapped[str | None] = mapped_column(String(255))
    process_initiator_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"), index=True, nullable=False
    )
    bpmn_process_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("bpmn_process_definition.id"),
        index=True,
    )
    bpmn_process_id: Mapped[int | None] = mapped_column(
        ForeignKey("bpmn_process.id"),
        index=True,
    )
    workflow_engine_version: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False, default="running"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    task_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bpmn_version_control_type: Mapped[str | None] = mapped_column(String(50))
    bpmn_version_control_identifier: Mapped[str | None] = mapped_column(String(255))
    last_milestone_bpmn_name: Mapped[str | None] = mapped_column(
        String(255), index=True
    )
    bpmn_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("process_model_bpmn_version.id"),
        index=True,
    )

    process_initiator = relationship("UserModel")
    bpmn_process_definition = relationship("BpmnProcessDefinitionModel")
    bpmn_process = relationship("BpmnProcessModel")
    tasks = relationship(
        "TaskModel", back_populates="process_instance", cascade="all, delete-orphan"
    )
    work_items = relationship(
        "WorkItemModel",
        back_populates="process_instance",
        cascade="all, delete-orphan",
    )
    active_work_items = relationship(
        "WorkItemModel",
        primaryjoin=(
            "and_(WorkItemModel.process_instance_id == ProcessInstanceModel.id, "
            "WorkItemModel.completed == False)"
        ),
        viewonly=True,
    )
    process_instance_events = relationship(
        "ProcessInstanceEventModel",
        back_populates="process_instance",
        cascade="all, delete-orphan",
    )
    process_metadata = relationship(
        "ProcessInstanceMetadataModel",
        back_populates="process_instance",
        cascade="all, delete-orphan",
    )
    scheduler_jobs = relationship(
        "SchedulerJobModel",
        back_populates="process_instance",
        cascade="all, delete-orphan",
    )

    def has_terminal_status(self) -> bool:
        """Return whether the instance is complete, errored or terminated."""
        return self.status in _TERMINAL_STATUSES

    @classmethod
    def active_statuses(cls) -> list[str]:
        """Return the status values of live instances, excluding suspended."""
        return list(_ACTIVE_STATUSES)

    @validates("status")
    def validate_status(self, key: str, value: Any) -> Any:
        return ProcessInstanceStatus(value).value

    @property
    def workflow_state_json(self) -> str | None:
        if self.bpmn_process is None:
            return None
        session = object_session(self)
        if session is None:
            return None
        json_data = JsonDataModel.get_for_tenant(
            session,
            self.bpmn_process.m8f_tenant_id,
            self.bpmn_process.json_data_hash,
        )
        if json_data is None:
            return None
        value = json_data.data.get(WORKFLOW_STATE_JSON_DATA_KEY)
        return value if isinstance(value, str) else None
