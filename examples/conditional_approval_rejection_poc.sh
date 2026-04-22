#!/usr/bin/env bash

set -euo pipefail

DATABASE_URL=""
USE_DOCKER=0
POSTGRES_IMAGE="${M8FLOW_EXAMPLE_POSTGRES_IMAGE:-postgres:16}"
KEEP_CONTAINER=0
STARTED_CONTAINER_NAME=""
PYTHON_RUNNER=()

main() {
  parse_args "$@"

  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  local repo_root
  repo_root="$(cd -- "$script_dir/.." && pwd -P)"
  local example_script="$script_dir/conditional_approval_rejection_poc.py"
  local default_database_url="postgresql+psycopg://postgres:postgres@localhost:5432/m8flow_bpmn_core_example?connect_timeout=1"

  if [[ ! -f "$example_script" ]]; then
    echo "Could not find the Python example at $example_script" >&2
    exit 1
  fi

  resolve_python_runner "$repo_root"

  trap cleanup EXIT INT TERM

  local example_database_url
  example_database_url="$(resolve_example_database_url "$default_database_url")"
  export M8FLOW_EXAMPLE_DATABASE_URL="$example_database_url"

  printf '\n'
  printf '%s\n' 'Status: launching the interactive conditional-approval rejection example...'
  printf 'Status: using database URL %s\n' "$(mask_database_url "$example_database_url")"

  if "${PYTHON_RUNNER[@]}" "$example_script"; then
    :
  else
    local exit_code=$?
    printf 'Status: the example exited with code %s.\n' "$exit_code"
    exit "$exit_code"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --database-url)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --database-url" >&2
          exit 1
        fi
        DATABASE_URL="$2"
        shift 2
        ;;
      --database-url=*)
        DATABASE_URL="${1#*=}"
        shift
        ;;
      --docker)
        USE_DOCKER=1
        shift
        ;;
      --postgres-image)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --postgres-image" >&2
          exit 1
        fi
        POSTGRES_IMAGE="$2"
        shift 2
        ;;
      --postgres-image=*)
        POSTGRES_IMAGE="${1#*=}"
        shift
        ;;
      --keep-container)
        KEEP_CONTAINER=1
        shift
        ;;
      -h|--help)
        print_usage
        exit 0
        ;;
      --)
        shift
        break
        ;;
      *)
        echo "Unknown argument: $1" >&2
        print_usage >&2
        exit 1
        ;;
    esac
  done
}

print_usage() {
  cat <<'EOF'
Usage:
  ./examples/conditional_approval_rejection_poc.sh [--database-url URL] [--docker] [--postgres-image IMAGE] [--keep-container]

Options:
  --database-url URL      Use an explicit PostgreSQL URL.
  --docker                Force a temporary Docker Postgres container.
  --postgres-image IMAGE   Override the Docker image used for the temporary container.
  --keep-container        Leave the temporary container running after the example exits.
EOF
}

resolve_python_runner() {
  local repo_root="$1"
  if command -v uv >/dev/null 2>&1; then
    PYTHON_RUNNER=(uv run python)
    return 0
  fi

  if [[ -x "$repo_root/.venv/bin/python" ]]; then
    PYTHON_RUNNER=("$repo_root/.venv/bin/python")
    return 0
  fi

  if [[ -x "$repo_root/.venv/Scripts/python.exe" ]]; then
    PYTHON_RUNNER=("$repo_root/.venv/Scripts/python.exe")
    return 0
  fi

  echo "Could not find uv or the project virtual environment." >&2
  exit 1
}

resolve_example_database_url() {
  local default_database_url="$1"

  if [[ -n "$DATABASE_URL" ]]; then
    printf '%s\n' "Status: using the database URL passed to the launcher." >&2
    printf '%s\n' "$DATABASE_URL"
    return 0
  fi

  if [[ "$USE_DOCKER" -eq 1 ]]; then
    start_temporary_postgres_container
    return 0
  fi

  if [[ -n "${M8FLOW_EXAMPLE_DATABASE_URL:-}" ]]; then
    printf '%s\n' 'Status: using M8FLOW_EXAMPLE_DATABASE_URL from the environment.' >&2
    printf '%s\n' "$M8FLOW_EXAMPLE_DATABASE_URL"
    return 0
  fi

  printf '%s\n' 'Status: checking whether the default local Postgres database is reachable...' >&2
  if check_database_url "$default_database_url"; then
    printf '%s\n' 'Status: default local Postgres database is reachable.' >&2
    printf '%s\n' "$default_database_url"
    return 0
  fi

  printf '%s\n' 'Status: default local database is not reachable, starting Docker fallback...' >&2
  start_temporary_postgres_container
}

start_temporary_postgres_container() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not available, so the example cannot start a fallback Postgres container." >&2
    exit 1
  fi

  local container_suffix
  container_suffix="$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 8)"
  STARTED_CONTAINER_NAME="m8flow-bpmn-core-example-${container_suffix}"
  printf 'Status: starting temporary Docker Postgres container %s...\n' "$STARTED_CONTAINER_NAME" >&2
  docker run -d --rm \
    --name "$STARTED_CONTAINER_NAME" \
    -e "POSTGRES_USER=postgres" \
    -e "POSTGRES_HOST_AUTH_METHOD=trust" \
    -e "POSTGRES_DB=m8flow_bpmn_core_example" \
    -P "$POSTGRES_IMAGE" >/dev/null

  local mapped_port
  mapped_port="$(get_container_host_port "$STARTED_CONTAINER_NAME")"
  printf 'Status: temporary container is available on host port %s.\n' "$mapped_port" >&2
  printf 'postgresql+psycopg://postgres@127.0.0.1:%s/m8flow_bpmn_core_example\n' "$mapped_port"
}

get_container_host_port() {
  local container_name="$1"
  local output
  output="$(docker port "$container_name" 5432/tcp)"

  local line
  while IFS= read -r line; do
    if [[ "$line" =~ ([0-9]+)$ ]]; then
      printf '%s\n' "${BASH_REMATCH[1]}"
      return 0
    fi
  done <<< "$output"

  echo "Could not parse the mapped port for container $container_name." >&2
  exit 1
}

check_database_url() {
  local database_url="$1"
  "${PYTHON_RUNNER[@]}" - "$database_url" <<'PY'
import sys

from sqlalchemy import create_engine, text


url = sys.argv[1]
engine = create_engine(url)
try:
    with engine.connect() as connection:
        connection.execute(text("select 1"))
finally:
    engine.dispose()
PY
}

mask_database_url() {
  local database_url="$1"
  printf '%s\n' "$database_url" | sed -E 's#://([^:/]+):([^@]+)@#://\1:***@#'
}

cleanup() {
  if [[ -n "$STARTED_CONTAINER_NAME" && "$KEEP_CONTAINER" -eq 0 ]]; then
    printf 'Status: removing temporary Docker container %s...\n' "$STARTED_CONTAINER_NAME" >&2
    docker rm -f "$STARTED_CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
}

main "$@"
