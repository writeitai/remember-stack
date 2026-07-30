#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  echo "usage: $0 HOSTS_FILE REMOTE_RUN_DIR DATASET_PATH COLLECTION_DIR" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

if [[ $# -ne 4 ]]; then
  usage
  exit 2
fi

hosts_file=$1
remote_run_dir=$2
dataset_path=$3
collection_dir=$4
python_bin=${LOCOMO_PYTHON:-.venv/bin/python}

[[ -f "$hosts_file" ]] || die "host list does not exist: $hosts_file"
[[ -f "$dataset_path" ]] || die "dataset does not exist: $dataset_path"
[[ -x "$python_bin" ]] || die "Python is not executable: $python_bin"
[[ "$remote_run_dir" != *[[:space:]]* ]] ||
  die "remote run directory may not contain whitespace"
if [[ -d "$collection_dir" ]] &&
  find "$collection_dir" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
  die "collection directory must be empty: $collection_dir"
fi
mkdir -p "$collection_dir/runs"

run_dirs=()
host_index=0
while IFS= read -r host || [[ -n "$host" ]]; do
  [[ -n "$host" && "$host" != \#* ]] || continue
  [[ "$host" != *[[:space:]]* ]] || die "host entries may not contain whitespace"
  [[ "$host" != -* ]] || die "host entries may not start with a dash"
  host_index=$((host_index + 1))
  safe_host=${host//[^A-Za-z0-9._-]/_}
  local_run_dir=$(
    printf '%s/runs/%02d-%s' "$collection_dir" "$host_index" "$safe_host"
  )
  mkdir -p "$local_run_dir"
  printf 'collect host=%s destination=%s\n' "$host" "$local_run_dir"
  if command -v rsync >/dev/null 2>&1; then
    rsync --archive --partial "$host:$remote_run_dir/" "$local_run_dir/"
  else
    scp -r "$host:$remote_run_dir/." "$local_run_dir/"
  fi
  [[ -f "$local_run_dir/run.json" ]] ||
    die "collected run has no run.json: $local_run_dir"
  "$python_bin" - "$local_run_dir/run.json" "$dataset_path" <<'PY'
import json
import os
from pathlib import Path
import sys

run_path = Path(sys.argv[1])
dataset_path = str(Path(sys.argv[2]).resolve())
payload = json.loads(run_path.read_text(encoding="utf-8"))
payload["dataset_path"] = dataset_path
temporary = run_path.with_name(f".{run_path.name}.tmp")
temporary.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
os.replace(temporary, run_path)
PY
  run_dirs+=("$local_run_dir")
done <"$hosts_file"

[[ ${#run_dirs[@]} -gt 0 ]] || die "host list contains no hosts"
summarize_args=()
for run_dir in "${run_dirs[@]}"; do
  summarize_args+=(--run "$run_dir")
done

summary_path="$collection_dir/summary.json"
summary_json=$(
  "$python_bin" -m benchmarks.locomo summarize "${summarize_args[@]}"
)
printf '%s\n' "$summary_json" >"$summary_path"
printf 'summary=%s\n' "$summary_path"
"$python_bin" - "$summary_path" <<'PY'
import json
from pathlib import Path
import sys

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    "score "
    f"judge={summary['judge_percent']:.2f}% "
    f"official_f1={summary['official_f1']:.6f} "
    f"questions={summary['questions']} "
    f"runs={summary.get('merged_run_count', 1)}"
)
PY
