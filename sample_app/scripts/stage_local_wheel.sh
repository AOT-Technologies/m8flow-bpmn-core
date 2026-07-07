#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dist_dir="${1:-"$script_dir/../../dist"}"
vendor_dir="$script_dir/../vendor"
pyproject_path="$script_dir/../pyproject.toml"

wheel_path="$(ls -1t "$dist_dir"/m8flow_bpmn_core-*.whl 2>/dev/null | head -n 1 || true)"

if [[ -z "${wheel_path}" ]]; then
  echo "No m8flow_bpmn_core wheel was found in '$dist_dir'. Run 'uv build' from the repo root first." >&2
  exit 1
fi

mkdir -p "$vendor_dir"
rm -f "$vendor_dir"/m8flow_bpmn_core-*.whl "$vendor_dir"/m8flow_bpmn_core.whl
destination="$vendor_dir/$(basename "$wheel_path")"
cp "$wheel_path" "$destination"

relative_wheel_path="vendor/$(basename "$wheel_path")"
python3 - "$pyproject_path" "$relative_wheel_path" <<'PY'
from pathlib import Path
import re
import sys

pyproject_path = Path(sys.argv[1])
relative_wheel_path = sys.argv[2]
original_text = pyproject_path.read_text(encoding="utf-8")
pattern = r'm8flow-bpmn-core = \{ path = "vendor/[^"]+" \}'
if re.search(pattern, original_text) is None:
    raise SystemExit(
        f"Could not update '{pyproject_path}' with the staged wheel path."
    )
updated_text = re.sub(
    pattern,
    f'm8flow-bpmn-core = {{ path = "{relative_wheel_path}" }}',
    original_text,
    count=1,
)
pyproject_path.write_text(updated_text, encoding="utf-8")
PY

echo "Staged wheel: $wheel_path"
echo "Destination : $destination"
echo "Updated source: $relative_wheel_path"
