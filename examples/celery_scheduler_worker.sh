#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
# shellcheck source=examples/_postgres_example_launcher.sh
source "$SCRIPT_DIR/_postgres_example_launcher.sh"

BROKER_URL=""
RESULT_BACKEND=""
DATABASE_URL=""
QUEUE_NAME=""
TENANT_ID=""
POLL_SECONDS="1"
LOG_LEVEL="info"
SKIP_BEAT=0
CELERY_RUNNER=()
BEAT_PID=""
BEAT_STDOUT_PATH=""
BEAT_STDERR_PATH=""

print_usage() {
  cat <<'EOF'
Usage:
  ./examples/celery_scheduler_worker.sh [--broker-url URL] [--result-backend URL] [--database-url URL] [--queue-name NAME] [--tenant-id ID] [--poll-seconds SECONDS] [--log-level LEVEL] [--skip-beat]

Options:
  --broker-url URL        Override the Celery broker URL.
  --result-backend URL    Override the Celery result backend URL.
  --database-url URL      Override the database URL used by the poller.
  --queue-name NAME       Override the Celery queue name.
  --tenant-id ID          Filter polling to a single tenant.
  --poll-seconds SECONDS  Override the scheduler poll interval.
  --log-level LEVEL       Override the Celery log level.
  --skip-beat             Run only the worker process and skip the local beat helper.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
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
      --skip-beat)
        SKIP_BEAT=1
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

resolve_celery_runner() {
  if command -v uv >/dev/null 2>&1; then
    CELERY_RUNNER=(uv run celery)
    return 0
  fi

  if [[ -x "$REPO_ROOT/.venv/bin/celery" ]]; then
    CELERY_RUNNER=("$REPO_ROOT/.venv/bin/celery")
    return 0
  fi

  if [[ -x "$REPO_ROOT/.venv/Scripts/celery.exe" ]]; then
    CELERY_RUNNER=("$REPO_ROOT/.venv/Scripts/celery.exe")
    return 0
  fi

  echo "Could not find celery in uv or the project virtual environment." >&2
  exit 1
}

configure_environment() {
  if [[ -n "$BROKER_URL" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_BROKER_URL="$BROKER_URL"
  elif [[ -z "${M8FLOW_BPMN_CORE_CELERY_BROKER_URL:-}" && -z "${M8FLOW_BACKEND_CELERY_BROKER_URL:-}" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_BROKER_URL="redis://localhost:6848/0"
  fi

  if [[ -n "$RESULT_BACKEND" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND="$RESULT_BACKEND"
  elif [[ -z "${M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND:-}" && -z "${M8FLOW_BACKEND_CELERY_RESULT_BACKEND:-}" ]]; then
    if [[ -n "${M8FLOW_BPMN_CORE_CELERY_BROKER_URL:-}" ]]; then
      export M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND="$M8FLOW_BPMN_CORE_CELERY_BROKER_URL"
    fi
  fi

  if [[ -n "$DATABASE_URL" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_DATABASE_URL="$DATABASE_URL"
  elif [[ -z "${M8FLOW_BPMN_CORE_CELERY_DATABASE_URL:-}" && -z "${M8FLOW_EXAMPLE_DATABASE_URL:-}" && -z "${M8FLOW_DATABASE_URL:-}" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:6843/postgres?connect_timeout=1"
  fi

  if [[ -n "$QUEUE_NAME" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_QUEUE="$QUEUE_NAME"
  elif [[ -z "${M8FLOW_BPMN_CORE_CELERY_QUEUE:-}" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_QUEUE="m8flow-bpmn-core-poc"
  fi

  if [[ -n "$TENANT_ID" ]]; then
    export M8FLOW_BPMN_CORE_CELERY_TENANT_ID="$TENANT_ID"
  fi

  export M8FLOW_BPMN_CORE_CELERY_POLL_SECONDS="$POLL_SECONDS"
}

cleanup() {
  if [[ -n "$BEAT_PID" ]] && kill -0 "$BEAT_PID" 2>/dev/null; then
    printf 'Status: stopping Celery beat helper PID %s...\n' "$BEAT_PID"
    kill "$BEAT_PID" 2>/dev/null || true
    wait "$BEAT_PID" 2>/dev/null || true
  fi
}

start_beat() {
  BEAT_STDOUT_PATH="$(mktemp "${TMPDIR:-/tmp}/m8flow-bpmn-core-celery-beat.XXXXXX.out.log")"
  BEAT_STDERR_PATH="$(mktemp "${TMPDIR:-/tmp}/m8flow-bpmn-core-celery-beat.XXXXXX.err.log")"
  local beat_schedule_path
  beat_schedule_path="$(mktemp "${TMPDIR:-/tmp}/m8flow-bpmn-core-celery-beat.XXXXXX.schedule")"

  printf '%s\n' 'Status: starting a Celery beat helper...'
  "${CELERY_RUNNER[@]}" -A examples.celery_scheduler_poc:celery_app beat \
    --loglevel "$LOG_LEVEL" \
    --schedule "$beat_schedule_path" \
    >"$BEAT_STDOUT_PATH" 2>"$BEAT_STDERR_PATH" &
  BEAT_PID=$!
  sleep 2

  if ! kill -0 "$BEAT_PID" 2>/dev/null; then
    local beat_stdout=""
    local beat_stderr=""
    if [[ -f "$BEAT_STDOUT_PATH" ]]; then
      beat_stdout="$(cat "$BEAT_STDOUT_PATH")"
    fi
    if [[ -f "$BEAT_STDERR_PATH" ]]; then
      beat_stderr="$(cat "$BEAT_STDERR_PATH")"
    fi
    echo "The Celery beat helper exited immediately. Stdout:
$beat_stdout
Stderr:
$beat_stderr" >&2
    exit 1
  fi

  printf 'Status: Celery beat helper PID %s is running.\n' "$BEAT_PID"
  printf 'Status: Celery beat stdout log %s\n' "$BEAT_STDOUT_PATH"
  printf 'Status: Celery beat stderr log %s\n' "$BEAT_STDERR_PATH"
}

main() {
  parse_args "$@"
  cd "$REPO_ROOT"
  resolve_celery_runner
  configure_environment
  trap cleanup EXIT INT TERM

  printf '%s\n' 'Status: starting the Celery scheduler worker helper...'
  printf '%s\n' 'Status: this script only runs the scheduler poller. Use celery_timer_poc.sh for the full workflow POC.'
  printf 'Status: broker URL %s\n' "${M8FLOW_BPMN_CORE_CELERY_BROKER_URL:-${M8FLOW_BACKEND_CELERY_BROKER_URL:-}}"
  printf 'Status: result backend %s\n' "${M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND:-${M8FLOW_BACKEND_CELERY_RESULT_BACKEND:-}}"
  if [[ -n "${M8FLOW_BPMN_CORE_CELERY_DATABASE_URL:-}" ]]; then
    printf 'Status: database URL %s\n' "$(m8f_mask_database_url "$M8FLOW_BPMN_CORE_CELERY_DATABASE_URL")"
  else
    printf '%s\n' 'Status: database URL inherited from M8FLOW_EXAMPLE_DATABASE_URL or M8FLOW_DATABASE_URL'
  fi
  printf 'Status: queue %s\n' "$M8FLOW_BPMN_CORE_CELERY_QUEUE"
  if [[ -n "${M8FLOW_BPMN_CORE_CELERY_TENANT_ID:-}" ]]; then
    printf 'Status: tenant filter %s\n' "$M8FLOW_BPMN_CORE_CELERY_TENANT_ID"
  fi
  printf 'Status: poll interval %ss\n' "$M8FLOW_BPMN_CORE_CELERY_POLL_SECONDS"

  if [[ "$SKIP_BEAT" -eq 0 ]]; then
    start_beat
  fi

  local worker_exit_code
  if "${CELERY_RUNNER[@]}" -A examples.celery_scheduler_poc:celery_app worker \
    --pool solo \
    --loglevel "$LOG_LEVEL" \
    -Q "$M8FLOW_BPMN_CORE_CELERY_QUEUE"; then
    worker_exit_code=0
  else
    worker_exit_code=$?
  fi

  if [[ "$worker_exit_code" -ne 0 ]]; then
    exit "$worker_exit_code"
  fi
}

main "$@"
