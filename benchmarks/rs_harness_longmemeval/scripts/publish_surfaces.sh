#!/usr/bin/env bash
# Build P2/P3, attempt K surfaces, publish mounts + skill for the rs arm workspace.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RUN_DIR="${1:?usage: publish_surfaces.sh RUN_DIR}"
API_URL="${REMEMBERSTACK_API_URL:-http://127.0.0.1:8000}"
MOUNT_ROOT="${REMEMBERSTACK_MOUNT_ROOT:-$RUN_DIR/mounts-host}"

cd "$ROOT"
echo "Building projections (P2+P3)…"
docker compose --profile operations run --rm projections

echo "Publishing mounts under $MOUNT_ROOT …"
mkdir -p "$MOUNT_ROOT"
# Prefer a small Python helper that calls selfhost mount publisher when available.
uv run python - <<PY || true
from pathlib import Path
import json
import os

run_dir = Path("$RUN_DIR")
state_path = run_dir / "state.json"
state = json.loads(state_path.read_text())
# Placeholder surfaces record — filled when operator mounts manually.
state["surfaces"] = {
    "api_url": "$API_URL",
    "mount_root": "$MOUNT_ROOT",
    "p3": str(Path("$MOUNT_ROOT") / "p3"),
    "k": str(Path("$MOUNT_ROOT") / "k"),
    "k_page_count": None,
    "note": "Run local mount publisher after projections; wire k_page_count from Plane K checkout.",
}
state_path.write_text(json.dumps(state, indent=2) + "\n")
print(json.dumps(state["surfaces"], indent=2))
PY

# Symlink into Claude Code rs workspace
WS_RS="$RUN_DIR/workspaces/rs"
mkdir -p "$WS_RS/mounts" "$WS_RS/.claude/skills/rememberstack"
ln -sfn "$MOUNT_ROOT/p3" "$WS_RS/mounts/p3" 2>/dev/null || mkdir -p "$WS_RS/mounts/p3"
ln -sfn "$MOUNT_ROOT/k" "$WS_RS/mounts/k" 2>/dev/null || mkdir -p "$WS_RS/mounts/k"

# MCP config for Claude Code in the rs workspace
cat > "$WS_RS/.mcp.json" <<EOF
{
  "mcpServers": {
    "rememberstack": {
      "command": "uv",
      "args": ["--directory", "$ROOT", "run", "remember", "mcp"],
      "env": {
        "REMEMBERSTACK_API_URL": "$API_URL"
      }
    }
  }
}
EOF

cat > "$WS_RS/.claude/skills/rememberstack/SKILL.md" <<'EOF'
# RememberStack (harness skill)

Orient on Plane K (`mounts/k`) first when pages exist.
Browse the corpus on P3 (`mounts/p3`) with ls/read/grep.
Use MCP tools from the rememberstack server for semantic search, graph, and hydration.
Claims are testimony; relations/observations are current facts. Verify load-bearing answers on the fact layer.
If K is empty, say so and fall back to P3 then MCP — do not invent synthesis.
EOF

echo "Wrote MCP + skill under $WS_RS"
echo "Next: ensure P3/K trees exist at $MOUNT_ROOT/{p3,k}, then run_cc --arm rs"
