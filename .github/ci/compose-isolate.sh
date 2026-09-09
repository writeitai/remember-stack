#!/usr/bin/env bash
# Give this Compose job its own project name and host ports. Two quickstart
# jobs on umc-gha-01 otherwise both bind 8000 and 9001.
set -euo pipefail

# shellcheck source=ci-slot.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ci-slot.sh"

slot="$(ci_slot_id)"
api_port="$(ci_host_port "${slot}" 35000 5000)"
minio_console="$(ci_host_port "${slot}" 30000 5000)"
# Per-slot project name so the runner job-started hook can tear leftovers down.
project="rs-qs-$(ci_runner_label | tr -c 'A-Za-z0-9_.-' '-')"

if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    echo "COMPOSE_PROJECT_NAME=${project}"
    echo "REMEMBERSTACK_SELFHOST_API_PORT=${api_port}"
    echo "REMEMBERSTACK_MINIO_CONSOLE_PORT=${minio_console}"
    echo "REMEMBERSTACK_SELFHOST_API_URL=http://127.0.0.1:${api_port}"
  } >> "${GITHUB_ENV}"
fi

echo "Compose project ${project} api=${api_port} minio-console=${minio_console}"
