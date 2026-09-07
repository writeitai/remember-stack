"""Fixed, versioned memory-arm instruction. The native arm never receives it."""

from __future__ import annotations

import hashlib
from typing import Final

from benchmarks.workspacebench.protocol import CONSUMPTION_INSTRUCTION_VERSION

MEMORY_CONSUMPTION_INSTRUCTION: Final = """# RememberStack Workspace-Bench memory consumption (v1.1.0)

You still have the complete native workspace filesystem. RememberStack is an
augmentation, not a replacement filesystem. Produce the required output
artifacts by inspecting and editing local files.

When Remember MCP tools are listed for this session, use only those listed
tools to locate, relate, and verify workspace information:

- `resolve_entity` maps a name to canonical entity candidates.
- `claims_and_sources_context` retrieves high-recall current claims and source
  chunks (what sources said).
- `facts_context` retrieves current or historical adjudicated facts.
- `combined_context` returns `ContextBundle/v2` with separate
  `claims_and_sources` and `facts` child envelopes — two complete authorities,
  never a blended result list.
- Open-query tools (`query_sql` and the other listed query tools) only when
  they appear in the tool list.

Rules:
- Do not call `ingest` or `pipeline_readiness`; they are not available.
- Do not upload task outputs, conversation state, or generated files into memory.
- Treat memory results as nominations. Confirm load-bearing facts against the
  native files before writing outputs.
- Do not search the web, spawn subagents, or call MCP servers other than
  `remember`.
- Stay inside this task workspace. Do not escalate network or approvals.

This instruction is the only added treatment relative to the native arm.
"""


def consumption_instruction_sha256() -> str:
    """Return the canonical SHA-256 of the versioned memory instruction."""
    payload = (
        f"{CONSUMPTION_INSTRUCTION_VERSION}\n{MEMORY_CONSUMPTION_INSTRUCTION}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()
