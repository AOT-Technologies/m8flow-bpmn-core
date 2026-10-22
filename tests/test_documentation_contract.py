from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
# The upgrade guide is excluded on purpose: it names the removed fields to
# explain how to migrate from them.
PUBLIC_DOCUMENTATION = (
    ROOT / "docs" / "reference" / "api.md",
    ROOT / "docs" / "reference" / "database-schema.md",
    ROOT / "docs" / "tutorials" / "quick-start.md",
    ROOT / "docs" / "how-to" / "embed-in-your-application.md",
    ROOT / "docs" / "how-to" / "build-and-install-the-package.md",
    ROOT / "docs" / "how-to" / "run-timers-and-retries.md",
    ROOT / "docs" / "explanation" / "scheduling.md",
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
