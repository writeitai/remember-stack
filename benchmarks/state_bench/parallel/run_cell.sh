#!/usr/bin/env bash
# Run one STATE-Bench evaluation cell bound to a prepared run.json.
# Parallelism:
#   - within cell: STATE --num-workers
#   - across cells: invoke this script many times (one process per domain/arm)
set -Eeuo pipefail

usage() {
  echo "usage: $0 --run-dir DIR --domain DOMAIN --num-workers N [--execute]" >&2
}

log() {
  printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

run_dir=""
domain=""
num_workers="${STATE_NUM_WORKERS:-8}"
execute=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) run_dir=$2; shift 2 ;;
    --domain) domain=$2; shift 2 ;;
    --num-workers) num_workers=$2; shift 2 ;;
    --execute) execute=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$run_dir" ]] || die "--run-dir is required"
[[ -n "$domain" ]] || die "--domain is required"
[[ -f "$run_dir/run.json" ]] || die "missing $run_dir/run.json (run prepare first)"
[[ -f "$run_dir/manifest.json" ]] || die "missing $run_dir/manifest.json"
[[ "$execute" == 1 ]] || die "refusing to spend: pass --execute after preflight"

# Resolve fingerprint fields from run.json / manifest.json (not free-form flags).
read -r state_bench_root arm sub_protocol agent_model_name num_runs top_k recipe_name domains_csv task_csv <<EOF
$(python3 - "$run_dir" "$domain" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
domain = sys.argv[2]
run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
if domain not in run["domains"]:
    raise SystemExit(f"domain {domain!r} not in run.json domains={run['domains']}")
if domain not in manifest["domains"]:
    raise SystemExit(f"domain {domain!r} not in manifest.json")
tasks = manifest["domains"][domain]
if not tasks:
    raise SystemExit(f"no task ids for domain {domain}")
print(
    run["state_bench_root"],
    run["arm"],
    run["sub_protocol"],
    run["agent_model_name"],
    run["num_runs"],
    run["top_k"],
    run["recipe_name"],
    ",".join(run["domains"]),
    ",".join(tasks),
)
PY
)
EOF

[[ -d "$state_bench_root" ]] || die "STATE-Bench root missing: $state_bench_root"

case "$arm" in
  empty) agent_class=EmptyMemoryAgent ;;
  bm25) agent_class=Bm25MemoryAgent ;;
  rememberstack) agent_class=RememberStackMemoryAgent ;;
  *) die "no agent class mapped for arm=$arm (implement or use empty/bm25/rememberstack)" ;;
esac

# Domain-scoped documents for BM25 / operator ingest.
domain_docs="$run_dir/documents/$domain/documents.json"
[[ -f "$domain_docs" ]] || die "missing domain documents: $domain_docs"

# Install adapter agents once under flock (safe for concurrent cells).
agents_src="$(cd "$(dirname "$0")/../agents" && pwd)"
agents_dst="$state_bench_root/agents"
mkdir -p "$agents_dst"
(
  flock 9
  for agent_file in "$agents_src"/*.py; do
    base=$(basename "$agent_file")
    [[ "$base" == "__init__.py" ]] && continue
    ln -sfn "$agent_file" "$agents_dst/$base"
  done
) 9>"$agents_dst/.rs_state_agents.lock"

# Prefer the RememberStack venv when present so MemoryClient deps resolve.
repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
if [[ -x "$repo_root/.venv/bin/python" ]]; then
  python_bin="$repo_root/.venv/bin/python"
else
  python_bin="${LOCOMO_PYTHON:-python3}"
fi

export RS_STATE_DOCUMENTS_JSON="$domain_docs"
export RS_STATE_DOMAIN="$domain"
export RS_STATE_RECIPE="$recipe_name"
export PYTHONPATH="${repo_root}:${repo_root}/src:${PYTHONPATH:-}"

# Preflight: MemoryClient import for RS arm (fail before provider spend).
if [[ "$arm" == "rememberstack" ]]; then
  "$python_bin" - <<'PY' || die "RememberStack SDK not importable in this environment"
from rememberstack.surfaces.sdk import MemoryClient
print("MemoryClient import ok")
PY
fi

output_dir="$run_dir/outputs/$domain"
mkdir -p "$output_dir"
log "starting cell arm=$arm sub=$sub_protocol domain=$domain agent=$agent_class workers=$num_workers runs=$num_runs tasks=$(echo "$task_csv" | tr ',' '\n' | wc -l | tr -d ' ')"

# Run batch from STATE-Bench tree but with a Python that can see both packages.
# Operators should install rememberstack into the same env as state-bench, or
# use the RS venv with state-bench installed editable.
(
  cd "$state_bench_root"
  "$python_bin" -m state_bench.scripts.run_batch \
    --domain "$domain" \
    --agent-class "$agent_class" \
    --agent-model-name "$agent_model_name" \
    --num-runs "$num_runs" \
    --retrieve-learnings-top-k "$top_k" \
    --num-workers "$num_workers" \
    --tasks "$task_csv" \
    --output-dir "$output_dir"
)

log "finished cell domain=$domain -> $output_dir"
