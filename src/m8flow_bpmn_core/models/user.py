from __future__ import annotations

from datetime import datetime

from sqlalchemy import BIGINT, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from m8flow_bpmn_core.models.base import Base


class UserModel(Base):
    __tablename__ = "user"
    __timestamp_compatibility_pairs__ = (
        ("created_at_in_seconds", "created_at"),
        ("updated_at_in_seconds", "updated_at"),
    )
    __table_args__ = (
        UniqueConstraint("service", "service_id", name="m8f_user_service_id_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    service: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    service_id: Mapped[str] = mapped_column(
        String(255), index=True, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(255))
    tenant_specific_field_1: Mapped[str | None] = mapped_column(String(255))
    tenant_specific_field_2: Mapped[str | None] = mapped_column(String(255))
    tenant_specific_field_3: Mapped[str | None] = mapped_column(String(255))
    updated_at_in_seconds: Mapped[int | None] = mapped_column(BIGINT)
    created_at_in_seconds: Mapped[int | None] = mapped_column(BIGINT)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user_group_assignments = relationship(
        "UserGroupAssignmentModel",
        cascade="all, delete-orphan",
        back_populates="user",
    )
    groups = relationship(
        "GroupModel",
        viewonly=True,
        secondary="user_group_assignment",
        overlaps="user_group_assignments,user,group",
    )
    principal = relationship(
        "PrincipalModel",
        uselist=False,
        cascade="all, delete-orphan",
        back_populates="user",
    )
