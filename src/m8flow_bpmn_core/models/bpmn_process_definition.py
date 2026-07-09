from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped

SOURCE_BPMN_XML_PROPERTY_KEY = "__m8f_source_bpmn_xml"
SOURCE_DMN_XML_PROPERTY_KEY = "__m8f_source_dmn_xml"
PROCESS_MODEL_IDENTIFIER_PROPERTY_KEY = "__m8f_process_model_identifier"


class BpmnProcessDefinitionModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "bpmn_process_definition"
    __table_args__ = (
        UniqueConstraint(
            "m8f_tenant_id",
            "full_process_model_hash",
            name="bpmn_process_definition_full_process_model_hash_tenant_unique",
        ),
        UniqueConstraint(
            "m8f_tenant_id",
            "full_process_model_hash",
            "single_process_hash",
            name="process_hash_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    single_process_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_process_model_hash: Mapped[str | None] = mapped_column(String(255))
    bpmn_identifier: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    bpmn_name: Mapped[str | None] = mapped_column(String(255), index=True)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    bpmn_version_control_type: Mapped[str | None] = mapped_column(String(50))
    bpmn_version_control_identifier: Mapped[str | None] = mapped_column(String(255))
    updated_at_in_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at_in_seconds: Mapped[int | None] = mapped_column(Integer)

    bpmn_processes = relationship(
        "BpmnProcessModel",
        back_populates="bpmn_process_definition",
        cascade="all, delete-orphan",
    )
    task_definitions = relationship(
        "TaskDefinitionModel",
        back_populates="bpmn_process_definition",
        cascade="all, delete-orphan",
    )
    scheduler_jobs = relationship(
        "SchedulerJobModel",
        back_populates="bpmn_process_definition",
        cascade="all, delete-orphan",
    )

    @property
    def source_bpmn_xml(self) -> str | None:
        value = self.properties_json.get(SOURCE_BPMN_XML_PROPERTY_KEY)
        return value if isinstance(value, str) else None

    @source_bpmn_xml.setter
    def source_bpmn_xml(self, value: str | None) -> None:
        properties = dict(self.properties_json or {})
        if value is None:
            properties.pop(SOURCE_BPMN_XML_PROPERTY_KEY, None)
        else:
            properties[SOURCE_BPMN_XML_PROPERTY_KEY] = value
        self.properties_json = properties

    @property
    def source_dmn_xml(self) -> str | None:
        value = self.properties_json.get(SOURCE_DMN_XML_PROPERTY_KEY)
        return value if isinstance(value, str) else None

    @source_dmn_xml.setter
    def source_dmn_xml(self, value: str | None) -> None:
        properties = dict(self.properties_json or {})
        if value is None:
            properties.pop(SOURCE_DMN_XML_PROPERTY_KEY, None)
        else:
            properties[SOURCE_DMN_XML_PROPERTY_KEY] = value
        self.properties_json = properties

    @property
    def explicit_process_model_identifier(self) -> str | None:
        value = (self.properties_json or {}).get(
            PROCESS_MODEL_IDENTIFIER_PROPERTY_KEY
        )
        return value if isinstance(value, str) else None

    @property
    def process_model_identifier(self) -> str:
        explicit_identifier = self.explicit_process_model_identifier
        if explicit_identifier:
            return explicit_identifier
        return self.bpmn_identifier

    @process_model_identifier.setter
    def process_model_identifier(self, value: str) -> None:
        properties = dict(self.properties_json or {})
        properties[PROCESS_MODEL_IDENTIFIER_PROPERTY_KEY] = value
        self.properties_json = properties
