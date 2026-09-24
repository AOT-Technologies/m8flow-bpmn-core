from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapper

from m8flow_bpmn_core.models.timestamps import synchronize_timestamp_fields

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@event.listens_for(Base, "before_insert", propagate=True)
def _synchronize_timestamps_before_insert(
    mapper: Mapper[Any], connection: Connection, target: Any
) -> None:
    synchronize_timestamp_fields(mapper, target, inserting=True)


@event.listens_for(Base, "before_update", propagate=True)
def _synchronize_timestamps_before_update(
    mapper: Mapper[Any], connection: Connection, target: Any
) -> None:
    synchronize_timestamp_fields(mapper, target, inserting=False)
