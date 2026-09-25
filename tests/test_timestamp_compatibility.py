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


def test_timezone_aware_columns_are_present_for_all_compatibility_pairs() -> None:
    for mapper in Base.registry.mappers:
        pairs = getattr(mapper.class_, "__timestamp_compatibility_pairs__", ())
        for _legacy_name, native_name in pairs:
            native_type = mapper.columns[native_name].type
            assert native_type.timezone is True


def test_native_datetime_is_canonical_but_legacy_epoch_remains_compatible(
    session,
) -> None:
    user = UserModel(
        username="future-user",
        service="test",
        service_id="future-user",
        created_at_in_seconds=1,
        created_at=POST_2038,
        updated_at_in_seconds=1,
    )
    session.add(user)
    session.flush()

    assert user.created_at == POST_2038
    assert user.created_at_in_seconds == 2_209_033_800

    later = datetime(2041, 1, 1, 12, 30, tzinfo=UTC)
    user.updated_at = later
    session.flush()

    assert user.updated_at == later
    assert user.updated_at_in_seconds == datetime_to_epoch(later, integer=True)
