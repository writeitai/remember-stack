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
protocol=${LOCOMO_PROTOCOL:-full-v21}
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
runner_lock=${LOCOMO_RUNNER_LOCK:-/var/lock/rememberstack-locomo-shard.lock}
extract_claim_workers=${LOCOMO_EXTRACT_CLAIM_WORKERS:-8}
normalize_relation_workers=${LOCOMO_NORMALIZE_RELATION_WORKERS:-6}
adjudicate_observation_workers=${LOCOMO_ADJUDICATE_OBSERVATION_WORKERS:-4}
embed_claim_workers=${LOCOMO_EMBED_CLAIM_WORKERS:-2}
backup_tool=${LOCOMO_BACKUP_TOOL:-benchmarks/locomo/sharding/store_backup.py}
compose=(docker compose --project-name "$compose_project")

export GOOGLE_APPLICATION_CREDENTIALS=${LOCOMO_GCP_CREDENTIALS_FILE:-/etc/rememberstack/locomo-gcs/credentials.json}
export GOOGLE_API_CERTIFICATE_CONFIG=${LOCOMO_GCP_CERTIFICATE_CONFIG_FILE:-/etc/rememberstack/locomo-gcs/certificate-config.json}
export GOOGLE_API_USE_CLIENT_CERTIFICATE=true

# RS-LoCoMo-Full-v21's non-secret ingest identity. Override ambient self-host
# defaults so every shard runs the exact Luna/Qwen pipeline the protocol checks.
export REMEMBERSTACK_STRUCTURER_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_SKELETON_CHECK_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_ROLE_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_SUMMARY_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_E1_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
export REMEMBERSTACK_E1_PREFIX_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_E2_EXTRACT_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_E3_NORMALIZE_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_OBS_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
export REMEMBERSTACK_OBS_SMALL_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_OBS_FRONTIER_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_ADJUDICATOR_SMALL_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_ADJUDICATOR_FRONTIER_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_P1_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
export REMEMBERSTACK_P1_LABEL_MODEL=z-ai/glm-5.3-flash
export REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER=nebius
unset REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER
export REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS=32000
unset REMEMBERSTACK_OPENROUTER_REASONING_EFFORT
export REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP='{"z-ai/glm-5.3-flash":"high"}'
export REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ONLY=z-ai,novita,deepinfra,gmicloud
export REMEMBERSTACK_OPENROUTER_INVALID_COMPLETION_CAPTURE_DIR=/var/lib/rememberstack/invalid-completions

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
command -v flock >/dev/null || die "flock must be installed before a sharded run"
exec 9>"$runner_lock"
flock --nonblock 9 || die "another LoCoMo shard runner owns this host: $runner_lock"
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
  "$drain_poll_seconds" \
  "$extract_claim_workers" \
  "$normalize_relation_workers" \
  "$adjudicate_observation_workers" \
  "$embed_claim_workers"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "integer caps must be positive"
done

attest_worker_environment() {
  local app_count=0
  local container_id
  local environment
  local service
  local variable
  local expected
  local actual
  local -a frozen_variables=(
    REMEMBERSTACK_BUILD_REVISION
    REMEMBERSTACK_STRUCTURER_MODEL
    REMEMBERSTACK_SKELETON_CHECK_MODEL
    REMEMBERSTACK_ROLE_MODEL
    REMEMBERSTACK_SUMMARY_MODEL
    REMEMBERSTACK_E1_EMBEDDING_MODEL
    REMEMBERSTACK_E1_PREFIX_MODEL
    REMEMBERSTACK_E2_EXTRACT_MODEL
    REMEMBERSTACK_E3_NORMALIZE_MODEL
    REMEMBERSTACK_OBS_EMBEDDING_MODEL
    REMEMBERSTACK_OBS_SMALL_MODEL
    REMEMBERSTACK_OBS_FRONTIER_MODEL
    REMEMBERSTACK_ADJUDICATOR_SMALL_MODEL
    REMEMBERSTACK_ADJUDICATOR_FRONTIER_MODEL
    REMEMBERSTACK_P1_EMBEDDING_MODEL
    REMEMBERSTACK_P1_LABEL_MODEL
    REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ONLY
    REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER
    REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER
    REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS
    REMEMBERSTACK_OPENROUTER_REASONING_EFFORT
    REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP
    REMEMBERSTACK_OPENROUTER_INVALID_COMPLETION_CAPTURE_DIR
  )

  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    environment=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id")
    if ! grep -q '^REMEMBERSTACK_E2_EXTRACT_MODEL=' <<<"$environment"; then
      continue
    fi
    app_count=$((app_count + 1))
    service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")
    for variable in "${frozen_variables[@]}"; do
      expected=${!variable-}
      actual=$(sed -n "s/^${variable}=//p" <<<"$environment")
      [[ "$actual" == "$expected" ]] ||
        die "runtime binding mismatch service=$service variable=$variable"
    done
  done < <("${compose[@]}" ps --status running --quiet)
  ((app_count > 0)) || die "runtime binding attestation found no app containers"
  log "runtime-bindings status=verified app_containers=$app_count"
}

bind_benchmark_api() {
  local published
  local port
  published=$("${compose[@]}" port api 8000 | head -n 1)
  port=${published##*:}
  [[ "$port" =~ ^[1-9][0-9]*$ ]] && ((port <= 65535)) ||
    die "could not resolve the Compose API host port: $published"
  export REMEMBERSTACK_API_URL="http://127.0.0.1:$port"
  log "benchmark-api status=bound port=$port"
}

IFS=',' read -r -a requested_samples <<<"$sample_csv"
[[ ${#requested_samples[@]} -gt 0 ]] || die "the shard sample list is empty"
declare -A seen_samples=()
for sample_id in "${requested_samples[@]}"; do
  [[ "$sample_id" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$ ]] ||
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
documents = json.loads((run_dir / "documents.json").read_text(encoding="utf-8"))
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
sample_source_refs = {
    document["source_ref"]
    for document in documents
    if document["sample_id"] == sample_id
}
ingested_source_refs = {
    source_ref
    for source_ref, record in state["ingests"].items()
    if record["sample_id"] == sample_id
}
ingested = bool(sample_source_refs) and ingested_source_refs == sample_source_refs
partial = (
    any(record["sample_id"] == sample_id for record in state["ingests"].values())
    or any(item_id in state["answers"] for item_id in item_ids)
    or any(item_id in state["judges"] for item_id in item_ids)
)
print(
    "complete"
    if complete
    else "ingested"
    if ingested
    else "partial"
    if partial
    else "empty"
)
PY
}

sample_scoring_started() {
  "$python_bin" - "$run_dir" "$1" <<'PY'
import json
from pathlib import Path
import sys

run_dir = Path(sys.argv[1])
sample_id = sys.argv[2]
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
item_ids = {
    item_id
    for item_id in manifest["item_ids"]
    if item_id.startswith(f"{sample_id}/")
}
started = bool(item_ids.intersection(state["answers"])) or bool(
    item_ids.intersection(state["judges"])
)
print("yes" if started else "no")
PY
}

live_sample_id() {
  local marker=$run_dir/.locomo-live-store.json
  [[ -f "$marker" ]] || return 0
  "$python_bin" - "$marker" <<'PY'
import json
from pathlib import Path
import sys

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(value["sample_id"])
PY
}

backup_sample() {
  local sample_id=$1
  local checkpoint=${2:-final}
  "$python_bin" "$backup_tool" backup \
    --sample "$sample_id" \
    --run-dir "$run_dir" \
    --mount-root "$mount_root" \
    --compose-project "$compose_project" \
    --project "$backup_project" \
    --destination "$backup_destination" \
    --staging-root "$backup_staging_root" \
    --receipt-checkpoint "$checkpoint" \
    --lock-fd 9
}

require_verified_scoring_backup() {
  local sample_id=$1
  if ! "$python_bin" "$backup_tool" authorize-scoring \
    --run-dir "$run_dir" \
    --sample "$sample_id" \
    --compose-project "$compose_project" \
    --destination "$backup_destination" \
    --lock-fd 9; then
    return 1
  fi
  log "sample=$sample_id backup=verified scoring=authorized"
}

require_verified_final_backup() {
  local sample_id=$1
  "$python_bin" "$backup_tool" authorize-wipe \
    --run-dir "$run_dir" \
    --compose-project "$compose_project" \
    --lock-fd 9 ||
    die "sample=$sample_id has no current final backup; refusing completion"
}

start_existing_store() {
  "${compose[@]}" up --detach --wait --no-recreate \
    --scale "worker-extract-claims=$extract_claim_workers" \
    --scale "worker-normalize-relations=$normalize_relation_workers" \
    --scale "worker-adjudicate-observations=$adjudicate_observation_workers" \
    --scale "worker-embed-claim=$embed_claim_workers"
  bind_benchmark_api
  attest_worker_environment
}

stop_store_after_failed_scoring_authorization() {
  log "scoring authorization failed after restart; stopping store"
  "${compose[@]}" stop --timeout 120 || true
  return 0
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
  receipt=$run_dir/.locomo-backups/receipts/final/$sample_id.json
  if [[ -f "$receipt" ]] &&
    "$python_bin" "$backup_tool" authorize-wipe \
      --run-dir "$run_dir" \
      --compose-project "$compose_project" \
      --lock-fd 9; then
    return 0
  fi
  log "sample=$sample_id stage=final-backup status=resuming-after-completed-checkpoint"
  backup_sample "$sample_id" final
  require_verified_final_backup "$sample_id"
}

pending_samples=()
marked_sample=$(live_sample_id)
for sample_id in "${requested_samples[@]}"; do
  status=$(sample_status "$sample_id")
  case "$status" in
    complete)
      log "sample=$sample_id status=already-complete"
      ;;
    empty)
      pending_samples+=("$sample_id")
      ;;
    ingested)
      [[ "$sample_id" == "$marked_sample" ]] ||
        die "sample=$sample_id is ingested but does not own the marked live store"
      log "sample=$sample_id status=resumable-ingested-checkpoint"
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
marked_pending=false
for sample_id in "${pending_samples[@]}"; do
  [[ "$sample_id" == "$marked_sample" ]] && marked_pending=true
done
if [[ "$marked_pending" == true ]] && [[ "$(sample_status "$marked_sample")" == ingested ]]; then
  ordered_pending=("$marked_sample")
  for sample_id in "${pending_samples[@]}"; do
    [[ "$sample_id" == "$marked_sample" ]] || ordered_pending+=("$sample_id")
  done
  pending_samples=("${ordered_pending[@]}")
fi
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
    attest_worker_environment
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
  status=$(sample_status "$sample_id")
  if [[ "$status" == empty ]]; then
    "$python_bin" "$backup_tool" authorize-wipe \
      --run-dir "$run_dir" \
      --compose-project "$compose_project" \
      --lock-fd 9
    log "sample=$sample_id stage=wipe status=starting"
    "${compose[@]}" down --volumes --remove-orphans
    "$python_bin" "$backup_tool" clear-live --run-dir "$run_dir" --lock-fd 9
    log "sample=$sample_id stage=stack status=starting"
    "${compose[@]}" up --detach --wait \
      --scale "worker-extract-claims=$extract_claim_workers" \
      --scale "worker-normalize-relations=$normalize_relation_workers" \
      --scale "worker-adjudicate-observations=$adjudicate_observation_workers" \
      --scale "worker-embed-claim=$embed_claim_workers"
    bind_benchmark_api
    attest_worker_environment
    "$python_bin" "$backup_tool" record-live \
      --run-dir "$run_dir" \
      --sample "$sample_id" \
      --compose-project "$compose_project" \
      --lock-fd 9

    log "sample=$sample_id stage=ingest status=starting"
    "$python_bin" -m benchmarks.locomo ingest \
      --run "$run_dir" \
      --sample "$sample_id" \
      --max-documents "$max_documents" \
      --max-evaluator-cost-usd "$max_evaluator_cost_usd" \
      --execute \
      --confirm-isolated-deployment "$sample_id"
  elif [[ "$status" == ingested ]]; then
    [[ "$sample_id" == "$(live_sample_id)" ]] ||
      die "sample=$sample_id cannot resume a different live store"
    if [[ "$(sample_scoring_started "$sample_id")" == yes ]]; then
      require_verified_scoring_backup "$sample_id"
      log "sample=$sample_id stage=stack status=resuming-existing-store"
      start_existing_store
      require_verified_scoring_backup "$sample_id" ||
        {
          stop_store_after_failed_scoring_authorization
          die "sample=$sample_id backup re-verification failed; refusing scoring"
        }
      log "sample=$sample_id stage=answer status=resuming-from-checkpoint"
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

      log "sample=$sample_id stage=final-backup status=starting"
      backup_sample "$sample_id" final
      require_verified_final_backup "$sample_id"
      log "sample=$sample_id status=complete"
      continue
    fi
    log "sample=$sample_id stage=stack status=resuming-existing-store"
    start_existing_store
  else
    die "sample=$sample_id cannot enter runner loop with status=$status"
  fi

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

  log "sample=$sample_id stage=post-ingest-backup status=starting"
  backup_sample "$sample_id" scoring-base
  require_verified_scoring_backup "$sample_id"

  log "sample=$sample_id stage=stack status=restarting-after-post-ingest-backup"
  start_existing_store
  require_verified_scoring_backup "$sample_id" ||
    {
      stop_store_after_failed_scoring_authorization
      die "sample=$sample_id backup re-verification failed; refusing scoring"
    }

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

  log "sample=$sample_id stage=final-backup status=starting"
  backup_sample "$sample_id" final
  require_verified_final_backup "$sample_id"
  log "sample=$sample_id status=complete"
done

log "shard status=complete samples=${pending_samples[*]}"
