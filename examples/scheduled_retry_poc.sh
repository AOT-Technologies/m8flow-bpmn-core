#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=examples/_postgres_example_launcher.sh
source "$SCRIPT_DIR/_postgres_example_launcher.sh"

print_usage() {
  cat <<'EOF'
Usage:
  ./examples/scheduled_retry_poc.sh [--database-url URL] [--docker] [--postgres-image IMAGE] [--keep-container]

Options:
  --database-url URL      Use an explicit PostgreSQL URL.
  --docker                Force a temporary Docker Postgres container.
  --postgres-image IMAGE  Override the Docker image used for the temporary container.
  --keep-container        Leave the temporary container running after the example exits.
EOF
}

main() {
  m8f_init_postgres_launcher_defaults
  m8f_parse_postgres_launcher_args "$@"
  m8f_run_python_example "$SCRIPT_DIR" "scheduled_retry_poc.py" "scheduled retry POC"
}

main "$@"
