from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class ProcessInstanceStatus(StrEnum):
    complete = "complete"
    error = "error"
    not_started = "not_started"
    running = "running"
    suspended = "suspended"
    terminated = "terminated"
    user_input_required = "user_input_required"
    waiting = "waiting"


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
    process_initiator_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), index=True
    )
    bpmn_process_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("bpmn_process_definition.id"),
        index=True,
    )
    bpmn_process_id: Mapped[int | None] = mapped_column(
        ForeignKey("bpmn_process.id"),
        index=True,
    )
    process_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False, default="running"
    )
    start_in_seconds: Mapped[int | None] = mapped_column(Integer, index=True)
    end_in_seconds: Mapped[int | None] = mapped_column(Integer, index=True)
    updated_at_in_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at_in_seconds: Mapped[int | None] = mapped_column(Integer)

    process_initiator = relationship("UserModel")
    bpmn_process_definition = relationship("BpmnProcessDefinitionModel")
    bpmn_process = relationship("BpmnProcessModel")
    tasks = relationship(
        "TaskModel", back_populates="process_instance", cascade="all, delete-orphan"
    )
    human_tasks = relationship(
        "HumanTaskModel",
        back_populates="process_instance",
        cascade="all, delete-orphan",
        overlaps="active_human_tasks",
    )
    active_human_tasks = relationship(
        "HumanTaskModel",
        primaryjoin=(
            "and_(HumanTaskModel.process_instance_id == ProcessInstanceModel.id, "
            "HumanTaskModel.completed == False)"
        ),
        viewonly=True,
        overlaps="human_tasks",
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

    def can_submit_task(self) -> bool:
        return not self.has_terminal_status() and self.status != "suspended"

    def allowed_to_run(self) -> bool:
        return not self.has_terminal_status() and self.status != "suspended"

    def can_receive_message(self) -> bool:
        return not self.has_terminal_status() and self.status != "suspended"

    def has_terminal_status(self) -> bool:
        return self.status in self.terminal_statuses()

    def is_immediately_runnable(self) -> bool:
        return self.status in self.immediately_runnable_statuses()

    @classmethod
    def terminal_statuses(cls) -> list[str]:
        return ["complete", "error", "terminated"]

    @classmethod
    def non_terminal_statuses(cls) -> list[str]:
        terminal_status_values = cls.terminal_statuses()
        return [
            status.value
            for status in ProcessInstanceStatus
            if status.value not in terminal_status_values
        ]

    @classmethod
    def active_statuses(cls) -> list[str]:
        return cls.immediately_runnable_statuses() + ["user_input_required", "waiting"]

    @classmethod
    def immediately_runnable_statuses(cls) -> list[str]:
        return ["not_started", "running"]

    @validates("status")
    def validate_status(self, key: str, value: Any) -> Any:
        return ProcessInstanceStatus(value).value
