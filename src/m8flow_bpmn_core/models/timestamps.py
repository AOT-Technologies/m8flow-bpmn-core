from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, inspect
from sqlalchemy.orm import Mapper

TimestampPair = tuple[str, str]


def epoch_to_datetime(value: int | float | Decimal | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), UTC)


def datetime_to_epoch(
    value: datetime | None,
    *,
    integer: bool,
) -> int | float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    epoch = value.astimezone(UTC).timestamp()
    return int(round(epoch)) if integer else epoch


def synchronize_timestamp_fields(
    mapper: Mapper[Any],
    target: Any,
    *,
    inserting: bool,
) -> None:
    pairs: tuple[TimestampPair, ...] = getattr(
        target,
        "__timestamp_compatibility_pairs__",
        (),
    )
    if not pairs:
        return

    state = inspect(target)
    for legacy_name, native_name in pairs:
        legacy_value = getattr(target, legacy_name)
        native_value = getattr(target, native_name)
        legacy_changed = inserting or state.attrs[legacy_name].history.has_changes()
        native_changed = inserting or state.attrs[native_name].history.has_changes()

        if inserting:
            if native_value is not None:
                setattr(
                    target,
                    legacy_name,
                    datetime_to_epoch(
                        native_value,
                        integer=isinstance(mapper.columns[legacy_name].type, Integer),
                    ),
                )
            elif legacy_value is not None:
                setattr(target, native_name, epoch_to_datetime(legacy_value))
            continue

        if native_changed:
            setattr(
                target,
                legacy_name,
                datetime_to_epoch(
                    native_value,
                    integer=isinstance(mapper.columns[legacy_name].type, Integer),
                ),
            )
        elif legacy_changed:
            setattr(target, native_name, epoch_to_datetime(legacy_value))
