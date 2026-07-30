# Smoke notes — RS-Harness-MemEval-v1

## Environment

- Date: 2026-07-30
- Worktree: `ugm_3` / branch `feat/cc-rs-longmemeval-harness`
- Dataset: `xiaowu0162/longmemeval-cleaned` · `longmemeval_s_cleaned.json`
  - sha256 `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`
- Smoke prepare: 12 stratified items under `.benchmark-runs/harness-lme-smoke/`

## bare arm (Claude Code Max, no RS)

| Item | Question (short) | Gold | Pred | Notes |
|---|---|---|---|---|
| `001be529` | How long did I wait for the decision on my asylum application? | (from dataset) | **Unknown** | Correct bare behavior: no history tools → Unknown (~44s, exit 0) |

Command:

```bash
uv run python -m benchmarks.rs_harness_longmemeval run_cc \
  --run .benchmark-runs/harness-lme-smoke --arm bare --limit 1 --execute
```

## rs arm (MCP + P3 + K1)

**Not completed in this session.** Local `docker compose up --build` hit a concurrent build cancel while stamping many workers. Next steps:

1. `docker compose up -d` (retry; image already partially built as `0.2.0`)
2. Ingest smoke docs; drain; `projections` profile
3. Compile K1 + publish mounts (`publish_surfaces.sh`)
4. `run_cc --arm rs --limit 1 --execute`
5. Compare answers + require non-zero MCP/mount use

## Surfaces required for valid `rs` runs

- MCP: `remember mcp` → recipes
- P3 mount: `workspaces/rs/mounts/p3`
- K mount: `workspaces/rs/mounts/k` with **k_page_count > 0** after K1 compile
- Consumption skill under `workspaces/rs/.claude/skills/rememberstack/SKILL.md`
