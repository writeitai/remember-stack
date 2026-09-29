"""Fixed, versioned memory-arm instruction. The native arm never receives it."""

from __future__ import annotations

import hashlib
from typing import Final

from benchmarks.workspacebench.protocol import CONSUMPTION_INSTRUCTION_VERSION

MEMORY_CONSUMPTION_INSTRUCTION: Final = """# RememberStack Workspace-Bench memory consumption (v1.2.0)

You still have the complete native workspace filesystem. RememberStack is an
augmentation, not a replacement filesystem. Produce the required output
artifacts by inspecting and editing local files.

The `remember` MCP server is a read-only memory of this workspace. Use only
the tools it lists:

- To find files, call `search_documents` with a few words describing what
  you need. Each result's `source_path` or `file_name` names the file; open
  that file in the workspace and read it there.
- For facts, entities, and quoted evidence across files, use the context and
  entity tools (`combined_context`, `claims_and_sources_context`,
  `facts_context`, `resolve_entity`); `adjacent_chunks` widens a returned
  passage, and the query tools are there when listed.

Rules:
- Memory cannot be changed from this session; do not try to ingest or upload.
- Treat memory results as leads. Confirm load-bearing facts against the
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
