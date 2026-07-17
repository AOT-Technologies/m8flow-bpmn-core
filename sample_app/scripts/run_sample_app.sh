#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sample_app_root="$(cd "$script_dir/.." && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
repo_venv="$repo_root/.venv"
host="${M8FLOW_SAMPLE_APP_HOST:-127.0.0.1}"
port="${M8FLOW_SAMPLE_APP_PORT:-5010}"

if [[ -z "${UV_CACHE_DIR:-}" ]]; then
  export UV_CACHE_DIR="$sample_app_root/.uv-cache"
fi

use_active=0
if [[ "${1:-}" == "--active" ]]; then
  use_active=1
  shift
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  active_venv="$(cd "$VIRTUAL_ENV" && pwd 2>/dev/null || true)"
  if [[ -n "$active_venv" && "$active_venv" == "$repo_venv" ]]; then
    use_active=1
  fi
fi

if [[ $# -gt 0 ]]; then
  host="$1"
fi
if [[ $# -gt 1 ]]; then
  port="$2"
fi

sync_args=(sync)
run_args=(run)
if [[ $use_active -eq 1 ]]; then
  sync_args+=(--active)
  run_args+=(--active)
fi

echo
echo "==> Building the library wheel"
(
  cd "$repo_root"
  if ! uv build --wheel; then
    echo "uv build --wheel failed. Falling back to 'python -m build --wheel --no-isolation'." >&2
    python -m pip install build hatchling
    python -m build --wheel --no-isolation
  fi
)

echo
echo "==> Staging the newest wheel into sample_app/vendor"
(
  cd "$repo_root"
  bash "$script_dir/stage_local_wheel.sh"
)

echo
echo "==> Syncing the sample app environment"
(
  cd "$sample_app_root"
  uv "${sync_args[@]}"
)

echo
echo "==> Starting the sample app"
echo "Sample app URL: http://$host:$port"
(
  cd "$sample_app_root"
  export M8FLOW_SAMPLE_APP_HOST="$host"
  export M8FLOW_SAMPLE_APP_PORT="$port"
  uv "${run_args[@]}" m8flow-sample-app
)
