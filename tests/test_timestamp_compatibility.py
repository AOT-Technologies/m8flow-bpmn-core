from __future__ import annotations

from datetime import UTC, datetime

from m8flow_bpmn_core.models.base import Base
from m8flow_bpmn_core.models.timestamps import (
    datetime_to_epoch,
    epoch_to_datetime,
)
from m8flow_bpmn_core.models.user import UserModel

POST_2038 = datetime(2040, 1, 1, 12, 30, tzinfo=UTC)


def test_epoch_conversion_supports_post_2038_values() -> None:
    epoch = datetime_to_epoch(POST_2038, integer=True)

    assert epoch == 2_209_033_800
    assert epoch_to_datetime(epoch) == POST_2038


def test_timezone_aware_columns_are_present() -> None:
    for mapper in Base.registry.mappers:
        for column in mapper.columns:
            if column.name.endswith("_at") or column.name == "occurred_at":
                assert column.type.timezone is True


def test_native_datetime_is_canonical(
    session,
) -> None:
    user = UserModel(
        username="future-user",
        service="test",
        service_id="future-user",
        created_at=POST_2038,
        updated_at=POST_2038,
    )
    session.add(user)
    session.flush()

    assert user.created_at == POST_2038
    assert user.created_at.timestamp() == 2_209_033_800

    later = datetime(2041, 1, 1, 12, 30, tzinfo=UTC)
    user.updated_at = later
    session.flush()

    assert user.updated_at == later
