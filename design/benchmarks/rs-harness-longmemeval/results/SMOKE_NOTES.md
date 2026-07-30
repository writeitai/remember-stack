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

**Stack status:** Compose is up locally (API on host **:18000** → container :8000;
`/healthz` → `{"status":"ok"}`). Full E/P1 worker set running.

**Blocked for real ingest:** `REMEMBERSTACK_OPENROUTER_API_KEY` in local `.env` is still the
placeholder `replace-before-real-use` (length 23). Pipeline/K will not produce real claims
or K1 pages until a real key is set.

**Next steps when key is real:**

1. `export REMEMBERSTACK_API_URL=http://127.0.0.1:18000`
2. Ingest smoke docs; drain; `docker compose --profile operations run --rm projections`
3. Compile K1 + publish P3/K mounts (`publish_surfaces.sh`) — **k_page_count must be > 0**
4. `run_cc --arm rs --limit 1 --execute` with MCP + mounts
5. Score bare vs rs; require non-zero MCP/mount use on rs

## Surfaces required for valid `rs` runs

- MCP: `remember mcp` → recipes
- P3 mount: `workspaces/rs/mounts/p3`
- K mount: `workspaces/rs/mounts/k` with **k_page_count > 0** after K1 compile
- Consumption skill under `workspaces/rs/.claude/skills/rememberstack/SKILL.md`
