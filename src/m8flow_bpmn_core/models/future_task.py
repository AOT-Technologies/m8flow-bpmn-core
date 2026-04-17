from __future__ import annotations

import time

from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.sql import false

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.tenant_scoped import M8fTenantScopedMixin, TenantScoped


class FutureTaskModel(M8fTenantScopedMixin, TenantScoped, Base):
    __tablename__ = "future_task"

    guid: Mapped[str] = mapped_column(
        ForeignKey("task.guid", ondelete="CASCADE", name="future_task_task_guid_fk"),
        primary_key=True,
    )
    run_at_in_seconds: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    queued_to_run_at_in_seconds: Mapped[int | None] = mapped_column(
        Integer,
        index=True,
    )
    completed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    archived_for_process_instance_status: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
        index=True,
    )
    updated_at_in_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    task_model = relationship("TaskModel", back_populates="future_task")

    @classmethod
    def insert_or_update(
        cls,
        session: Session,
        *,
        tenant_id: str,
        guid: str,
        run_at_in_seconds: int,
        queued_to_run_at_in_seconds: int | None = None,
    ) -> None:
        task_info: dict[str, int | str | None] = {
            "m8f_tenant_id": tenant_id,
            "guid": guid,
            "run_at_in_seconds": run_at_in_seconds,
            "updated_at_in_seconds": round(time.time()),
        }
        if queued_to_run_at_in_seconds is not None:
            task_info["queued_to_run_at_in_seconds"] = queued_to_run_at_in_seconds

        new_values = task_info.copy()
        del new_values["guid"]

        bind = session.get_bind()
        if bind is None:
            raise RuntimeError(
                "FutureTaskModel.insert_or_update requires a bound session"
            )

        if bind.dialect.name == "mysql":
            insert_stmt = mysql_insert(cls).values(task_info)
            on_duplicate_key_stmt = insert_stmt.on_duplicate_key_update(**new_values)
        elif bind.dialect.name == "sqlite":
            insert_stmt = sqlite_insert(cls).values(task_info)
            on_duplicate_key_stmt = insert_stmt.on_conflict_do_update(
                index_elements=["guid"],
                set_=new_values,
            )
        elif bind.dialect.name == "postgresql":
            insert_stmt = postgres_insert(cls).values(task_info)
            on_duplicate_key_stmt = insert_stmt.on_conflict_do_update(
                index_elements=["guid"],
                set_=new_values,
            )
        else:
            session.merge(
                cls(
                    guid=guid,
                    m8f_tenant_id=tenant_id,
                    run_at_in_seconds=run_at_in_seconds,
                    queued_to_run_at_in_seconds=queued_to_run_at_in_seconds,
                    updated_at_in_seconds=task_info["updated_at_in_seconds"],
                )
            )
            return

        session.execute(on_duplicate_key_stmt)
