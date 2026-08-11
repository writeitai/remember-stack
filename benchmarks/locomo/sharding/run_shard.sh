#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  echo "usage: $0 SAMPLE_ID[,SAMPLE_ID...] RUN_DIR DATASET_PATH" >&2
}

log() {
  printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

if [[ $# -ne 3 ]]; then
  usage
  exit 2
fi

sample_csv=$1
run_dir=$2
dataset_path=$3
python_bin=${LOCOMO_PYTHON:-.venv/bin/python}
tier=${LOCOMO_TIER:-publication}
protocol=${LOCOMO_PROTOCOL:-full-v12}
mount_root=${LOCOMO_MOUNT_ROOT:-$run_dir/.mounts}
max_documents=${LOCOMO_MAX_DOCUMENTS:-100}
max_questions=${LOCOMO_MAX_QUESTIONS:-1540}
max_agent_calls=${LOCOMO_MAX_AGENT_CALLS:-13860}
max_judge_calls=${LOCOMO_MAX_JUDGE_CALLS:-1540}
max_evaluator_cost_usd=${LOCOMO_MAX_EVALUATOR_COST_USD:-1000}
drain_timeout_seconds=${LOCOMO_DRAIN_TIMEOUT_SECONDS:-21600}
drain_poll_seconds=${LOCOMO_DRAIN_POLL_SECONDS:-30}
backup_destination=${LOCOMO_BACKUP_DESTINATION:-}
backup_project=${LOCOMO_GCP_PROJECT:-}
backup_staging_root=${LOCOMO_BACKUP_STAGING_ROOT:-/var/lib/rememberstack-locomo-backups}
compose_project=${LOCOMO_COMPOSE_PROJECT:-rememberstack}
backup_tool=benchmarks/locomo/sharding/store_backup.py
compose=(docker compose)

export GOOGLE_APPLICATION_CREDENTIALS=${LOCOMO_GCP_CREDENTIALS_FILE:-/etc/rememberstack/locomo-gcs/credentials.json}
export GOOGLE_API_CERTIFICATE_CONFIG=${LOCOMO_GCP_CERTIFICATE_CONFIG_FILE:-/etc/rememberstack/locomo-gcs/certificate-config.json}
export GOOGLE_API_USE_CLIENT_CERTIFICATE=true

[[ -x "$python_bin" ]] || die "Python is not executable: $python_bin"
[[ -f "$dataset_path" ]] || die "dataset does not exist: $dataset_path"
[[ -n ${REMEMBERSTACK_OPENROUTER_API_KEY:-} ]] ||
  die "REMEMBERSTACK_OPENROUTER_API_KEY must be exported for the benchmark CLI"
[[ -n ${REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID:-} ]] ||
  die "REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID must be exported"
[[ -n "$backup_destination" ]] ||
  die "LOCOMO_BACKUP_DESTINATION must be a private gs:// bucket/base prefix"
[[ -n "$backup_project" ]] ||
  die "LOCOMO_GCP_PROJECT must identify the GCS billing project"
[[ -f "$backup_tool" ]] || die "backup tool does not exist: $backup_tool"
[[ $(id -u) -eq 0 ]] ||
  die "the sharded runner must run as root to archive Docker volume mountpoints"
command -v tar >/dev/null || die "tar must be installed before a sharded run"
command -v zstd >/dev/null || die "zstd must be installed before a sharded run"
[[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]] ||
  die "GCS workload credential configuration does not exist: $GOOGLE_APPLICATION_CREDENTIALS"
[[ -f "$GOOGLE_API_CERTIFICATE_CONFIG" ]] ||
  die "GCS certificate configuration does not exist: $GOOGLE_API_CERTIFICATE_CONFIG"
"$python_bin" "$backup_tool" preflight \
  --destination "$backup_destination" \
  --project "$backup_project" ||
  die "the configured keyless GCS destination is not readable"
for value in \
  "$max_documents" \
  "$max_questions" \
  "$max_agent_calls" \
  "$max_judge_calls" \
  "$drain_timeout_seconds" \
  "$drain_poll_seconds"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "integer caps must be positive"
done

IFS=',' read -r -a requested_samples <<<"$sample_csv"
[[ ${#requested_samples[@]} -gt 0 ]] || die "the shard sample list is empty"
declare -A seen_samples=()
for sample_id in "${requested_samples[@]}"; do
  [[ "$sample_id" =~ ^[A-Za-z0-9._-]+$ ]] ||
    die "invalid sample ID: $sample_id"
  [[ -z ${seen_samples[$sample_id]+present} ]] ||
    die "duplicate sample ID: $sample_id"
  seen_samples["$sample_id"]=1
done

export REMEMBERSTACK_BUILD_REVISION
REMEMBERSTACK_BUILD_REVISION=$(git rev-parse HEAD)

if [[ ! -f "$run_dir/run.json" ]]; then
  if [[ -d "$run_dir" ]] &&
    find "$run_dir" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    die "run directory is non-empty but has no run.json: $run_dir"
  fi
  log "preparing $tier run at $run_dir"
  "$python_bin" -m benchmarks.locomo prepare \
    --dataset "$dataset_path" \
    --tier "$tier" \
    --protocol "$protocol" \
    --output "$run_dir"
fi

sample_status() {
  "$python_bin" - "$run_dir" "$1" <<'PY'
import json
from pathlib import Path
import sys

run_dir = Path(sys.argv[1])
sample_id = sys.argv[2]
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
item_ids = [
    item_id
    for item_id in manifest["item_ids"]
    if item_id.startswith(f"{sample_id}/")
]
complete = (
    bool(item_ids)
    and all(item_id in state["answers"] for item_id in item_ids)
    and all(item_id in state["judges"] for item_id in item_ids)
)
partial = (
    any(record["sample_id"] == sample_id for record in state["ingests"].values())
    or any(item_id in state["answers"] for item_id in item_ids)
    or any(item_id in state["judges"] for item_id in item_ids)
)
print("complete" if complete else "partial" if partial else "empty")
PY
}

backup_sample() {
  local sample_id=$1
  "$python_bin" "$backup_tool" backup \
    --sample "$sample_id" \
    --run-dir "$run_dir" \
    --mount-root "$mount_root" \
    --deployment-id "$REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID" \
    --compose-project "$compose_project" \
    --project "$backup_project" \
    --destination "$backup_destination" \
    --staging-root "$backup_staging_root"
}

backup_completed_live_store() {
  local marker=$run_dir/.locomo-live-store.json
  local sample_id
  local status
  local receipt
  [[ -f "$marker" ]] || return 0
  sample_id=$(
    "$python_bin" - "$marker" <<'PY'
import json
from pathlib import Path
import sys

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(value["sample_id"])
PY
  )
  status=$(sample_status "$sample_id")
  [[ "$status" == complete ]] || return 0
  receipt=$run_dir/.locomo-backups/receipts/$sample_id.json
  if [[ -f "$receipt" ]]; then
    "$python_bin" "$backup_tool" verify --receipt "$receipt"
  else
    log "sample=$sample_id stage=backup status=resuming-after-completed-checkpoint"
    backup_sample "$sample_id"
  fi
}

pending_samples=()
for sample_id in "${requested_samples[@]}"; do
  status=$(sample_status "$sample_id")
  case "$status" in
    complete)
      log "sample=$sample_id status=already-complete"
      ;;
    empty)
      pending_samples+=("$sample_id")
      ;;
    partial)
      die "sample=$sample_id has a partial checkpoint; preserve its current stack and resume stages manually"
      ;;
    *)
      die "sample=$sample_id has unknown checkpoint status: $status"
      ;;
  esac
done
backup_completed_live_store
if [[ ${#pending_samples[@]} -eq 0 ]]; then
  log "shard complete; all requested samples were already checkpointed"
  exit 0
fi

wait_for_drain() {
  local started_at
  local now
  local elapsed
  local counts
  local busy
  local dead
  started_at=$(date +%s)
  while true; do
    counts=$(
      "${compose[@]}" exec -T postgres sh -c \
        'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc --tuples-only --no-align --command "$1"' \
        _ \
        "SELECT count(*) FILTER (WHERE status IN ('pending','running','failed')), count(*) FILTER (WHERE status = 'dead_letter') FROM processing_state;"
    )
    IFS='|' read -r busy dead <<<"$counts"
    [[ "$busy" =~ ^[0-9]+$ && "$dead" =~ ^[0-9]+$ ]] ||
      die "could not parse processing_state counts: $counts"
    log "pipeline busy=$busy dead_letter=$dead"
    [[ "$dead" == 0 ]] ||
      die "pipeline contains $dead dead-letter rows; preserving stack for inspection"
    if [[ "$busy" == 0 ]]; then
      return 0
    fi
    now=$(date +%s)
    elapsed=$((now - started_at))
    if ((elapsed >= drain_timeout_seconds)); then
      die "pipeline did not truly drain within ${drain_timeout_seconds}s"
    fi
    sleep "$drain_poll_seconds"
  done
}

for sample_id in "${pending_samples[@]}"; do
  "$python_bin" "$backup_tool" authorize-wipe \
    --run-dir "$run_dir" \
    --compose-project "$compose_project"
  log "sample=$sample_id stage=wipe status=starting"
  "${compose[@]}" down --volumes --remove-orphans
  "$python_bin" "$backup_tool" clear-live --run-dir "$run_dir"
  log "sample=$sample_id stage=stack status=starting"
  "${compose[@]}" up --detach --wait \
    --scale worker-extract-claims=3 \
    --scale worker-normalize-relations=6 \
    --scale worker-embed-claim=2
  "$python_bin" "$backup_tool" record-live \
    --run-dir "$run_dir" \
    --sample "$sample_id" \
    --compose-project "$compose_project"

  log "sample=$sample_id stage=ingest status=starting"
  "$python_bin" -m benchmarks.locomo ingest \
    --run "$run_dir" \
    --sample "$sample_id" \
    --max-documents "$max_documents" \
    --max-evaluator-cost-usd "$max_evaluator_cost_usd" \
    --execute \
    --confirm-isolated-deployment "$sample_id"

  log "sample=$sample_id stage=drain status=waiting"
  wait_for_drain

  log "sample=$sample_id stage=projections status=starting"
  "${compose[@]}" --profile operations run --rm projections

  log "sample=$sample_id stage=mounts status=starting"
  mkdir -p "$mount_root"
  mount_root=$(cd "$mount_root" && pwd -P)
  "${compose[@]}" --profile operations run --rm \
    --user "$(id -u):$(id -g)" \
    -v "$mount_root:$mount_root" \
    projections mounts --root "$mount_root"

  log "sample=$sample_id stage=answer status=starting"
  "$python_bin" -m benchmarks.locomo answer \
    --run "$run_dir" \
    --sample "$sample_id" \
    --p3-root "$mount_root/$REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID/p3" \
    --max-questions "$max_questions" \
    --max-agent-calls "$max_agent_calls" \
    --max-evaluator-cost-usd "$max_evaluator_cost_usd" \
    --execute

  log "sample=$sample_id stage=judge status=starting"
  "$python_bin" -m benchmarks.locomo judge \
    --run "$run_dir" \
    --sample "$sample_id" \
    --max-judge-calls "$max_judge_calls" \
    --max-evaluator-cost-usd "$max_evaluator_cost_usd" \
    --execute

  log "sample=$sample_id stage=backup status=starting"
  backup_sample "$sample_id"
  log "sample=$sample_id status=complete"
done

log "shard status=complete samples=${pending_samples[*]}"
