# RS-Harness-MemEval-v1

Claude Code **±** RememberStack (MCP + **P3** + **K1**) on **LongMemEval-S**.

Strategy: [`design/benchmarks/rs-harness-longmemeval/STRATEGY.md`](../../design/benchmarks/rs-harness-longmemeval/STRATEGY.md)

## Quick path

```bash
# 1) dataset
# download LongMemEval cleaned somewhere, e.g. /data/longmemeval-cleaned

# 2) prepare smoke
uv run python -m benchmarks.rs_harness_longmemeval prepare \
  --tier smoke \
  --dataset-root /data/longmemeval-cleaned \
  --output .benchmark-runs/harness-lme-smoke

# 3) stack up + ingest + surfaces (rs arm)
docker compose up -d
uv run python -m benchmarks.rs_harness_longmemeval ingest \
  --run .benchmark-runs/harness-lme-smoke --execute
# drain workers…
docker compose --profile operations run --rm projections
./benchmarks/rs_harness_longmemeval/scripts/publish_surfaces.sh \
  .benchmark-runs/harness-lme-smoke

# 4) Claude Code A/B (uses Max subscription via `claude -p`)
uv run python -m benchmarks.rs_harness_longmemeval run_cc \
  --run .benchmark-runs/harness-lme-smoke --arm bare --execute
uv run python -m benchmarks.rs_harness_longmemeval run_cc \
  --run .benchmark-runs/harness-lme-smoke --arm rs --execute

# 5) score
uv run python -m benchmarks.rs_harness_longmemeval score \
  --run .benchmark-runs/harness-lme-smoke
```
