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
protocol=${LOCOMO_PROTOCOL:-full-v6}
max_documents=${LOCOMO_MAX_DOCUMENTS:-100}
max_questions=${LOCOMO_MAX_QUESTIONS:-1540}
max_agent_calls=${LOCOMO_MAX_AGENT_CALLS:-13860}
max_judge_calls=${LOCOMO_MAX_JUDGE_CALLS:-1540}
max_evaluator_cost_usd=${LOCOMO_MAX_EVALUATOR_COST_USD:-1000}
drain_timeout_seconds=${LOCOMO_DRAIN_TIMEOUT_SECONDS:-21600}
drain_poll_seconds=${LOCOMO_DRAIN_POLL_SECONDS:-30}
fresh_stack=${LOCOMO_FRESH_STACK:-1}
compose=(docker compose)

[[ -x "$python_bin" ]] || die "Python is not executable: $python_bin"
[[ -f "$dataset_path" ]] || die "dataset does not exist: $dataset_path"
[[ -n ${REMEMBERSTACK_OPENROUTER_API_KEY:-} ]] ||
  die "REMEMBERSTACK_OPENROUTER_API_KEY must be exported for the benchmark CLI"
[[ "$fresh_stack" == 0 || "$fresh_stack" == 1 ]] ||
  die "LOCOMO_FRESH_STACK must be 0 or 1"
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
mkdir -p "$run_dir/forensics"

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
if [[ ${#pending_samples[@]} -eq 0 ]]; then
  log "shard complete; all requested samples were already checkpointed"
  exit 0
fi

dump_database() {
  local label=$1
  local timestamp
  local destination
  timestamp=$(date -u +'%Y%m%dT%H%M%SZ')
  destination="$run_dir/forensics/${label}-${timestamp}.sql"
  log "forensic-dump=$destination status=starting"
  "${compose[@]}" exec -T postgres sh -c \
    'pg_dumpall --username "$POSTGRES_USER"' >"$destination"
  log "forensic-dump=$destination status=complete"
}

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

first_sample=1
for sample_id in "${pending_samples[@]}"; do
  if [[ "$first_sample" == 1 ]]; then
    if [[ "$fresh_stack" == 0 ]]; then
      dump_database "pre-shard"
    else
      log "sample=$sample_id forensic-dump=skipped reason=fresh-stack"
    fi
    first_sample=0
  fi

  log "sample=$sample_id stage=wipe status=starting"
  "${compose[@]}" down --volumes --remove-orphans
  log "sample=$sample_id stage=stack status=starting"
  "${compose[@]}" up --detach --wait \
    --scale worker-extract-claims=3 \
    --scale worker-normalize-relations=6 \
    --scale worker-embed-claim=2

  log "sample=$sample_id stage=ingest status=starting"
  "$python_bin" -m benchmarks.locomo ingest \
    --run "$run_dir" \
    --sample "$sample_id" \
    --max-documents "$max_documents" \
    --execute \
    --confirm-isolated-deployment "$sample_id"

  log "sample=$sample_id stage=drain status=waiting"
  wait_for_drain

  log "sample=$sample_id stage=projections status=starting"
  "${compose[@]}" --profile operations run --rm projections

  log "sample=$sample_id stage=answer status=starting"
  "$python_bin" -m benchmarks.locomo answer \
    --run "$run_dir" \
    --sample "$sample_id" \
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

  dump_database "$sample_id"
  log "sample=$sample_id status=complete"
done

log "shard status=complete samples=${pending_samples[*]}"
