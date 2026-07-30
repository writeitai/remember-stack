# RS-STATE-Learning-v1 setup

RememberStack adapter for **STATE-Bench Agent Learning Track** (Phase 8 Track B).

This directory does **not** vendor STATE-Bench. Pin a local checkout to
`4efcbf2d4fe60df04878859b692d9391f3d5b33a` (`v0.8.1`).

Binding design: [`plan/designs/state_bench_benchmark_design.md`](../../plan/designs/state_bench_benchmark_design.md).

## Safe first command (no provider calls)

```bash
# Scored prepares require a clean git worktree. Use --allow-dirty only for local dev.
uv run python -m benchmarks.state_bench prepare \
  --tier smoke \
  --arm empty \
  --sub-protocol shared \
  --state-bench-root /absolute/path/to/STATE-Bench \
  --agent-model-name gpt-5.1 \
  --domain travel \
  --output .benchmark-runs/state-smoke-empty
```

RememberStack / BM25 arms require **exactly one** `--domain` (one deployment and
document pool per domain). Sub-protocol `native` is rejected until raw-trajectory
ingest is implemented.

This validates manifests, checks train/test leakage, serializes train
trajectories to markdown, and writes `run.json`.

## Parallel planning

```bash
uv run python -m benchmarks.state_bench plan-matrix \
  --tier smoke \
  --arm empty --arm rememberstack \
  --sub-protocol shared \
  --num-workers 8
```

See [`parallel/README.md`](parallel/README.md) for multi-domain / multi-arm
scheduling (`--num-workers` inside STATE + process-level domain matrix).

## Agents

| Class | Arm |
|---|---|
| `EmptyMemoryAgent` | empty control |
| `Bm25MemoryAgent` | lexical floor over `documents.json` |
| `RememberStackMemoryAgent` | public `claims_hybrid_rrf` (or `RS_STATE_RECIPE`) |

`parallel/run_cell.sh` symlinks these into `$STATE_BENCH_ROOT/agents/`.

## Status

Setup and pure tests only. No real locked-simulator smoke is authorized by the
design document until the owner completes the preflight checklist.
