# Runbook — RS-Harness-BEAM-v1

## Surfaces (rs arm) — full stack

| Must work | Access path |
|---|---|
| **P1 search** | MCP recipes (`claims_hybrid_rrf`, …) after embed pipeline |
| **P2 graph** | MCP graph recipes after `projections` / P2 build |
| **P3 files** | `mounts/p3` read-only tree |
| **K1 pages** | `mounts/k` + `pages_about` |
| **MCP** | `remember mcp` → full recipe list |
| **Skill** | orient (K) → navigate (P3) → retrieve (P1) → relate (P2) → verify |

Do **not** run a “valid rs” score if only P3 exists without P1 embeddings/recipes,
or without an attempted K compile.

## Scale subsets

Never run full BEAM. Manifests pin probe IDs per stage (S0–S3). Hero = **10M
subset** after smaller scales prove the surfaces.

## Claude Code

- Local Max OK for agent turns (`claude -p`).  
- RS pipeline still needs real OpenRouter (or equivalent) keys.  
- API URL: check host port (often `http://127.0.0.1:18000`).

## Commands (once package exists)

```bash
# prepare BEAM smoke subset
uv run python -m benchmarks.rs_harness_beam prepare \
  --scale 128k \          # or 1m / 10m when ready
  --tier smoke \
  --dataset-root /path/to/BEAM \
  --output .benchmark-runs/beam-smoke

# ingest + full surfaces
export REMEMBERSTACK_API_URL=http://127.0.0.1:18000
uv run python -m benchmarks.rs_harness_beam ingest --run ... --execute
# drain E/P1 workers, then:
docker compose --profile operations run --rm projections   # P2+P3
# K1 compile + mount publish (see package scripts)
./benchmarks/rs_harness_beam/scripts/publish_surfaces.sh .benchmark-runs/beam-smoke

# A/B
uv run python -m benchmarks.rs_harness_beam run_cc --arm bare --limit 4 --execute
uv run python -m benchmarks.rs_harness_beam run_cc --arm rs --limit 4 --execute
uv run python -m benchmarks.rs_harness_beam score --run ...
```
