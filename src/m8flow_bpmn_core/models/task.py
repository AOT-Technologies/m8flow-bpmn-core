from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


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
    state: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    json_data_hash: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    python_env_data_hash: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    runtime_info: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    start_in_seconds: Mapped[int | None] = mapped_column(Integer)
    end_in_seconds: Mapped[int | None] = mapped_column(Integer)

    bpmn_process = relationship("BpmnProcessModel", back_populates="tasks")
    process_instance = relationship("ProcessInstanceModel", back_populates="tasks")
    task_definition = relationship("TaskDefinitionModel")
    human_tasks = relationship(
        "HumanTaskModel", back_populates="task_model", cascade="all, delete-orphan"
    )
    future_task = relationship(
        "FutureTaskModel",
        back_populates="task_model",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )
