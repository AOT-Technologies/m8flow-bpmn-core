#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dist_dir="${1:-"$script_dir/../../dist"}"
vendor_dir="$script_dir/../vendor"
pyproject_path="$script_dir/../pyproject.toml"
uv_lock_path="$script_dir/../uv.lock"
metadata_script="$script_dir/update_local_wheel_metadata.py"

wheel_path="$(ls -1t "$dist_dir"/m8flow_bpmn_core-*.whl 2>/dev/null | head -n 1 || true)"

if [[ -z "${wheel_path}" ]]; then
  echo "No m8flow_bpmn_core wheel was found in '$dist_dir'. Run 'uv build' from the repo root first." >&2
  exit 1
fi

mkdir -p "$vendor_dir"
rm -f "$vendor_dir"/m8flow_bpmn_core-*.whl "$vendor_dir"/m8flow_bpmn_core.whl
destination="$vendor_dir/$(basename "$wheel_path")"
cp "$wheel_path" "$destination"

python3 "$metadata_script" \
  --pyproject-path "$pyproject_path" \
  --uv-lock-path "$uv_lock_path" \
  --wheel-path "$destination" \
  --uv-executable "uv"

echo "Staged wheel: $wheel_path"
echo "Destination : $destination"
echo "Updated source: vendor/$(basename "$wheel_path")"
echo "Refreshed lock: sample_app/uv.lock"
