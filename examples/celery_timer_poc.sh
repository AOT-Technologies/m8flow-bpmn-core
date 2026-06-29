#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
# shellcheck source=examples/_postgres_example_launcher.sh
source "$SCRIPT_DIR/_postgres_example_launcher.sh"

USE_EXISTING_WORKER=0
BROKER_URL=""
RESULT_BACKEND=""
QUEUE_NAME=""
TENANT_ID=""
POLL_SECONDS="1"
LOG_LEVEL="info"
WORKER_HELPER_PID=""
WORKER_STDOUT_PATH=""
WORKER_STDERR_PATH=""

print_usage() {
  cat <<'EOF'
Usage:
  ./examples/celery_timer_poc.sh [--database-url URL] [--docker] [--postgres-image IMAGE] [--keep-container] [--use-existing-worker] [--broker-url URL] [--result-backend URL] [--queue-name NAME] [--tenant-id ID] [--poll-seconds SECONDS] [--log-level LEVEL]

Options:
  --database-url URL      Use an explicit PostgreSQL URL.
  --docker                Force a temporary Docker Postgres container.
  --postgres-image IMAGE  Override the Docker image used for the temporary container.
  --keep-container        Leave the temporary container running after the example exits.
  --use-existing-worker   Reuse an already-running celery_scheduler_worker.sh process.
  --broker-url URL        Override the Celery broker URL when auto-starting the worker.
  --result-backend URL    Override the Celery result backend when auto-starting the worker.
  --queue-name NAME       Override the Celery queue name when auto-starting the worker.
  --tenant-id ID          Filter polling to a single tenant when auto-starting the worker.
  --poll-seconds SECONDS  Override the scheduler poll interval when auto-starting the worker.
  --log-level LEVEL       Override the Celery log level when auto-starting the worker.
EOF
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
      --use-existing-worker)
        USE_EXISTING_WORKER=1
        shift
        ;;
      --broker-url)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --broker-url" >&2
          exit 1
        fi
        BROKER_URL="$2"
        shift 2
        ;;
      --broker-url=*)
        BROKER_URL="${1#*=}"
        shift
        ;;
      --result-backend)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --result-backend" >&2
          exit 1
        fi
        RESULT_BACKEND="$2"
        shift 2
        ;;
      --result-backend=*)
        RESULT_BACKEND="${1#*=}"
        shift
        ;;
      --queue-name)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --queue-name" >&2
          exit 1
        fi
        QUEUE_NAME="$2"
        shift 2
        ;;
      --queue-name=*)
        QUEUE_NAME="${1#*=}"
        shift
        ;;
      --tenant-id)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --tenant-id" >&2
          exit 1
        fi
        TENANT_ID="$2"
        shift 2
        ;;
      --tenant-id=*)
        TENANT_ID="${1#*=}"
        shift
        ;;
      --poll-seconds)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --poll-seconds" >&2
          exit 1
        fi
        POLL_SECONDS="$2"
        shift 2
        ;;
      --poll-seconds=*)
        POLL_SECONDS="${1#*=}"
        shift
        ;;
      --log-level)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --log-level" >&2
          exit 1
        fi
        LOG_LEVEL="$2"
        shift 2
        ;;
      --log-level=*)
        LOG_LEVEL="${1#*=}"
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

cleanup() {
  m8f_restore_example_database_env

  if [[ -n "$WORKER_HELPER_PID" ]] && kill -0 "$WORKER_HELPER_PID" 2>/dev/null; then
    printf 'Status: stopping the temporary Celery scheduler worker tree rooted at PID %s...\n' "$WORKER_HELPER_PID"
    kill "$WORKER_HELPER_PID" 2>/dev/null || true
    wait "$WORKER_HELPER_PID" 2>/dev/null || true
  fi

  m8f_cleanup_temporary_container
}

start_celery_scheduler_worker() {
  local worker_script="$1"
  local database_url="$2"
  WORKER_STDOUT_PATH="$(mktemp "${TMPDIR:-/tmp}/m8flow-bpmn-core-celery-worker.XXXXXX.out.log")"
  WORKER_STDERR_PATH="$(mktemp "${TMPDIR:-/tmp}/m8flow-bpmn-core-celery-worker.XXXXXX.err.log")"

  local args=(
    "$worker_script"
    --database-url "$database_url"
    --poll-seconds "$POLL_SECONDS"
    --log-level "$LOG_LEVEL"
  )

  if [[ -n "$BROKER_URL" ]]; then
    args+=(--broker-url "$BROKER_URL")
  fi
  if [[ -n "$RESULT_BACKEND" ]]; then
    args+=(--result-backend "$RESULT_BACKEND")
  fi
  if [[ -n "$QUEUE_NAME" ]]; then
    args+=(--queue-name "$QUEUE_NAME")
  fi
  if [[ -n "$TENANT_ID" ]]; then
    args+=(--tenant-id "$TENANT_ID")
  fi

  bash "${args[@]}" >"$WORKER_STDOUT_PATH" 2>"$WORKER_STDERR_PATH" &
  WORKER_HELPER_PID=$!
  sleep 5

  if ! kill -0 "$WORKER_HELPER_PID" 2>/dev/null; then
    local worker_stdout=""
    local worker_stderr=""
    if [[ -f "$WORKER_STDOUT_PATH" ]]; then
      worker_stdout="$(cat "$WORKER_STDOUT_PATH")"
    fi
    if [[ -f "$WORKER_STDERR_PATH" ]]; then
      worker_stderr="$(cat "$WORKER_STDERR_PATH")"
    fi
    echo "The temporary Celery scheduler worker exited immediately. Stdout:
$worker_stdout
Stderr:
$worker_stderr" >&2
    exit 1
  fi
}

main() {
  m8f_init_postgres_launcher_defaults
  parse_args "$@"

  local example_script="$SCRIPT_DIR/celery_timer_poc.py"
  local worker_script="$SCRIPT_DIR/celery_scheduler_worker.sh"
  local default_database_url="postgresql+psycopg://postgres:postgres@localhost:6843/postgres?connect_timeout=1"

  if [[ ! -f "$example_script" ]]; then
    echo "Could not find the Python example at $example_script" >&2
    exit 1
  fi
  if [[ ! -f "$worker_script" ]]; then
    echo "Could not find the Celery scheduler worker helper at $worker_script" >&2
    exit 1
  fi

  cd "$REPO_ROOT"
  m8f_resolve_python_runner "$REPO_ROOT"
  m8f_capture_example_database_env
  trap cleanup EXIT INT TERM

  local example_database_url
  example_database_url="$(m8f_resolve_example_database_url "$default_database_url")"
  m8f_apply_example_database_env "$example_database_url"

  printf '\n'
  printf '%s\n' 'Status: launching the Celery timer POC...'
  printf 'Status: using database URL %s\n' "$(m8f_mask_database_url "$example_database_url")"

  if [[ "$USE_EXISTING_WORKER" -eq 1 ]]; then
    printf '%s\n' 'Status: using an already-running Celery scheduler worker.'
  else
    start_celery_scheduler_worker "$worker_script" "$example_database_url"
    printf '%s\n' 'Status: started the temporary Celery scheduler worker helper.'
    printf 'Status: worker helper PID %s\n' "$WORKER_HELPER_PID"
    printf 'Status: worker stdout log %s\n' "$WORKER_STDOUT_PATH"
    printf 'Status: worker stderr log %s\n' "$WORKER_STDERR_PATH"
  fi

  if "${PYTHON_RUNNER[@]}" "$example_script"; then
    :
  else
    local exit_code=$?
    printf 'Status: the example exited with code %s.\n' "$exit_code"
    exit "$exit_code"
  fi
}

main "$@"
