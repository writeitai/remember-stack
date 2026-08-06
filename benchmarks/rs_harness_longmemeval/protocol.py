"""RS-Harness-MemEval-v1 constants."""

from __future__ import annotations

from typing import Final

PROTOCOL_NAME: Final = "RS-Harness-MemEval-v1"
ADAPTER_VERSION: Final = "harness-lme-2026.07"
# LongMemEval cleaned HF pin — set when dataset is downloaded and hashed.
DATASET_NAME: Final = "longmemeval-s-cleaned"
# Placeholder until prepare stamps the real revision from disk.
DATASET_PIN_NOTE: Final = "pin HF revision in run.json at prepare time"

TIER_COUNTS: Final = {
    "smoke": 12,
    "development": 50,
    "publication": 500,
}

ARMS: Final = ("bare", "rs")

# Fixed agent instruction fragment (both arms).
# Full RS surface: P1 search + P2 graph via MCP; P3 + K via mounts.
AGENT_TASK_PREAMBLE: Final = """You answer one long-term memory question.
Use only information available through your tools and mounted files.
Do not invent session history. If you cannot find the answer, say Unknown.
Prefer short factual answers.

When RememberStack surfaces are available (use the full stack, not files only):
1. Orient on Plane K / K1 (mounts/k or pages_about).
2. Navigate P3 corpus files if useful (mounts/p3).
3. Retrieve with P1 search recipes via MCP (e.g. claims_hybrid_rrf, claims_verbatim).
4. Use P2 graph recipes via MCP when entities/relations matter.
5. Verify facts, hydrate evidence, then answer.
"""

