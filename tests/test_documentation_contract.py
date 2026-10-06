from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
PUBLIC_DOCUMENTATION = (
    ROOT / "doc" / "api.md",
    ROOT / "doc" / "usage.md",
    ROOT / "doc" / "package.md",
    ROOT / "doc" / "scheduling.md",
)
REMOVED_TIMESTAMP_NAMES = (
    "run_at_in_seconds",
    "locked_at_in_seconds",
    "created_at_in_seconds",
    "updated_at_in_seconds",
    "started_at_in_seconds",
    "completed_at_in_seconds",
    "retry_at_in_seconds",
    "start_in_seconds",
    "end_in_seconds",
)


def test_public_documentation_uses_datetime_timestamp_contract() -> None:
    violations = [
        f"{path.relative_to(ROOT)} contains {name}"
        for path in PUBLIC_DOCUMENTATION
        for name in REMOVED_TIMESTAMP_NAMES
        if name in path.read_text(encoding="utf-8")
    ]

    assert not violations, "Removed timestamp API names found: " + "; ".join(
        violations
    )
