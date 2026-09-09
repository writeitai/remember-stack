#!/usr/bin/env bash
# Shared job slot + host-port derivation for a shared Docker daemon.
# Four runner processes share one kernel port table on umc-gha-01; a fixed
# --publish 5432:5432 (or 8000/9000/9001) makes the second job fail to start.

ci_slot_id() {
  local raw="${CI_SLOT_KEY:-}"
  if [[ -z "${raw}" ]]; then
    raw="${GITHUB_RUN_ID:-local}-${GITHUB_JOB:-job}-${GITHUB_RUN_ATTEMPT:-1}"
  fi
  printf '%s' "${raw}" | tr -c 'A-Za-z0-9_.-' '-' | cut -c1-80
}

# Deterministic host port in [base, base+width).
ci_host_port() {
  local slot="$1"
  local base="$2"
  local width="$3"
  python3 -c 'import sys, zlib
slot, base, width = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
print(base + (zlib.crc32(slot.encode()) % width))
' "${slot}" "${base}" "${width}"
}

ci_runner_label() {
  printf '%s' "${RUNNER_NAME:-local}"
}
