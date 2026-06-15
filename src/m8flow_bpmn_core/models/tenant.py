from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from m8flow_bpmn_core.models.base import Base


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DELETED = "DELETED"


class M8flowTenantModel(Base):
    __tablename__ = "m8flow_tenant"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    status: Mapped[TenantStatus] = mapped_column(
        Enum(TenantStatus),
        default=TenantStatus.ACTIVE,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(
        String(255),
        default="system",
        nullable=False,
    )
    modified_by: Mapped[str] = mapped_column(
        String(255),
        default="system",
        nullable=False,
    )
    created_at_in_seconds: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    updated_at_in_seconds: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
