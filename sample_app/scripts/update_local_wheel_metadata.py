from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Update sample_app pyproject and uv.lock to point at a staged "
            "local m8flow-bpmn-core wheel."
        )
    )
    parser.add_argument("--pyproject-path", required=True)
    parser.add_argument("--uv-lock-path", required=True)
    parser.add_argument("--wheel-path", required=True)
    args = parser.parse_args()

    pyproject_path = Path(args.pyproject_path)
    uv_lock_path = Path(args.uv_lock_path)
    wheel_path = Path(args.wheel_path)

    wheel_filename = wheel_path.name
    relative_wheel_path = f"vendor/{wheel_filename}"
    wheel_version = _wheel_version_from_filename(wheel_filename)
    wheel_hash = _sha256_for_file(wheel_path)

    _update_pyproject(
        pyproject_path=pyproject_path,
        relative_wheel_path=relative_wheel_path,
    )
    _update_uv_lock(
        uv_lock_path=uv_lock_path,
        relative_wheel_path=relative_wheel_path,
        wheel_filename=wheel_filename,
        wheel_version=wheel_version,
        wheel_hash=wheel_hash,
    )
    return 0


def _update_pyproject(*, pyproject_path: Path, relative_wheel_path: str) -> None:
    original_text = pyproject_path.read_text(encoding="utf-8")
    updated_text, substitutions = re.subn(
        r'm8flow-bpmn-core = \{ path = "vendor/[^"]+" \}',
        f'm8flow-bpmn-core = {{ path = "{relative_wheel_path}" }}',
        original_text,
        count=1,
    )
    if substitutions != 1:
        raise RuntimeError(
            f"Could not update '{pyproject_path}' with the staged wheel path."
        )
    pyproject_path.write_text(updated_text, encoding="utf-8")


def _update_uv_lock(
    *,
    uv_lock_path: Path,
    relative_wheel_path: str,
    wheel_filename: str,
    wheel_version: str,
    wheel_hash: str,
) -> None:
    original_text = uv_lock_path.read_text(encoding="utf-8")

    package_pattern = re.compile(
        r'(\[\[package\]\]\r?\nname = "m8flow-bpmn-core"\r?\nversion = ")[^"]+("'
        r'\r?\nsource = \{ path = ")vendor/[^"]+(".*?filename = ")[^"]+("'
        r', hash = "sha256:)[^"]+(")',
        re.DOTALL,
    )
    updated_text, substitutions = package_pattern.subn(
        (
            r"\g<1>"
            f"{wheel_version}"
            r'\g<2>'
            f"{relative_wheel_path}"
            r'\g<3>'
            f"{wheel_filename}"
            r'\g<4>'
            f"{wheel_hash}"
            r'\g<5>'
        ),
        original_text,
        count=1,
    )
    if substitutions != 1:
        raise RuntimeError(
            f"Could not update the locked m8flow-bpmn-core wheel entry in '{uv_lock_path}'."
        )

    requires_dist_pattern = re.compile(
        r'(\{ name = "m8flow-bpmn-core", path = ")vendor/[^"]+(" \})'
    )
    updated_text, substitutions = requires_dist_pattern.subn(
        r"\g<1>" + relative_wheel_path + r"\g<2>",
        updated_text,
        count=1,
    )
    if substitutions != 1:
        raise RuntimeError(
            "Could not update the sample-app lockfile dependency path for "
            "m8flow-bpmn-core."
        )

    uv_lock_path.write_text(updated_text, encoding="utf-8")


def _sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wheel_version_from_filename(filename: str) -> str:
    match = re.match(
        r"^m8flow_bpmn_core-(?P<version>.+?)-[^-]+-[^-]+-[^-]+\.whl$",
        filename,
    )
    if match is None:
        raise RuntimeError(
            "Could not determine the wheel version from "
            f"'{filename}'."
        )
    return match.group("version")


if __name__ == "__main__":
    raise SystemExit(main())
