# Runbook — RS-Harness-MemEval-v1 (local Claude Code)

## Prerequisites

- Claude Code CLI logged in (Max OK for agent turns).
- Docker + Compose for RememberStack self-host.
- `REMEMBERSTACK_OPENROUTER_API_KEY` (or equivalent) for pipeline + K.
- Dataset: LongMemEval cleaned (S) — see `prepare` for pin.

## 0. Workspace

```bash
cd /Users/jpuc/code/moje/ultimate_memory/ugm_3/ugm
# env from sibling checkout if needed:
# set -a && source ../ugm/.env && set +a   # only if paths match
cp .env.example .env   # fill keys
```

## 1. Start stack

```bash
export REMEMBERSTACK_BUILD_REVISION=$(git rev-parse HEAD)
docker compose build
docker compose up -d
# wait for api health
curl -sf http://127.0.0.1:8000/health/live || curl -sf http://127.0.0.1:8000/docs | head
```

## 2. Prepare LongMemEval smoke

```bash
uv sync --extra server --extra k
uv run python -m benchmarks.rs_harness_longmemeval prepare \
  --tier smoke \
  --dataset-root /path/to/longmemeval-cleaned \
  --output .benchmark-runs/harness-lme-smoke
```

## 3. Ingest (rs arm only)

```bash
uv run python -m benchmarks.rs_harness_longmemeval ingest \
  --run .benchmark-runs/harness-lme-smoke \
  --execute \
  --confirm-isolated-deployment harness-lme-smoke
```

Wait for continuous workers to drain, then:

```bash
docker compose --profile operations run --rm projections
# K1 compile + mounts (see scripts/publish_surfaces.sh)
./benchmarks/rs_harness_longmemeval/scripts/publish_surfaces.sh
```

Record `k_page_count` and mount paths printed by the script.

## 4. Claude Code configuration (rs arm)

In the harness workspace (not necessarily the monorepo root):

`.mcp.json` (or project MCP config Claude Code reads):

```json
{
  "mcpServers": {
    "rememberstack": {
      "command": "uv",
      "args": ["run", "remember", "mcp"],
      "env": {
        "REMEMBERSTACK_API_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

Mount P3 + K into the workspace (symlink or bind):

```bash
# paths from publish_surfaces.sh
ln -sfn "$P3_MOUNT" harness-ws/mounts/p3
ln -sfn "$K_MOUNT" harness-ws/mounts/k
cp "$SKILL_PATH" harness-ws/.claude/skills/rememberstack/SKILL.md
```

## 5. Run bare arm

```bash
# no MCP rememberstack; no mounts
uv run python -m benchmarks.rs_harness_longmemeval run_cc \
  --run .benchmark-runs/harness-lme-smoke \
  --arm bare \
  --execute
```

## 6. Run rs arm

```bash
uv run python -m benchmarks.rs_harness_longmemeval run_cc \
  --run .benchmark-runs/harness-lme-smoke \
  --arm rs \
  --execute
```

## 7. Score

```bash
uv run python -m benchmarks.rs_harness_longmemeval score \
  --run .benchmark-runs/harness-lme-smoke
```

## Isolation rules

- Do not leave `REMEMBERSTACK_API_URL` set for bare arm MCP.
- Fresh Claude Code session per arm (or per item if variance is high).
- Do not hand the gold answer or evidence spans to the agent.
