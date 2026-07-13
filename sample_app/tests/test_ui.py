from __future__ import annotations

from m8flow_sample_app.ui import format_timestamp


def test_format_timestamp_renders_utc_seconds() -> None:
    assert format_timestamp(1783972071) == "2026-07-13 19:47:51 UTC"


def test_format_timestamp_renders_fractional_seconds() -> None:
    assert format_timestamp(1783972071.25) == "2026-07-13 19:47:51.250000 UTC"


def test_format_timestamp_handles_empty_values() -> None:
    assert format_timestamp(None) == ""
    assert format_timestamp("") == ""


def test_format_timestamp_falls_back_to_original_value_for_non_numeric_inputs() -> None:
    assert format_timestamp("not-a-timestamp") == "not-a-timestamp"
