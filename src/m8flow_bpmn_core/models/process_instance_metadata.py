from __future__ import annotations

from datetime import datetime

from sqlalchemy import BIGINT, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class ProcessInstanceMetadataModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "process_instance_metadata"
    __timestamp_compatibility_pairs__ = (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    )
    __table_args__ = (
        UniqueConstraint(
            "process_instance_id",
            "key",
            name="m8f_process_instance_metadata_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    process_instance_id: Mapped[int] = mapped_column(
        ForeignKey("process_instance.id"),
        index=True,
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at_in_seconds: Mapped[int] = mapped_column(BIGINT, nullable=False)
    created_at_in_seconds: Mapped[int] = mapped_column(BIGINT, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    process_instance = relationship(
        "ProcessInstanceModel",
        back_populates="process_metadata",
    )
