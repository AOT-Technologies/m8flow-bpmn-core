from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal


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
