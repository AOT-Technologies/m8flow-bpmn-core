from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from m8flow_bpmn_core.models.base import Base


class UserModel(Base):
    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("service", "service_id", name="service_key"),
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
    updated_at_in_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at_in_seconds: Mapped[int | None] = mapped_column(Integer)
