"""Pinned RS-STATE-Learning-v1 constants and pure helpers."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final
from typing import Mapping

PROTOCOL_NAME: Final = "RS-STATE-Learning-v1"
ADAPTER_VERSION: Final = "state-learning-adapter-2026.07"
STATE_BENCH_COMMIT: Final = "4efcbf2d4fe60df04878859b692d9391f3d5b33a"
STATE_BENCH_VERSION: Final = "v0.8.1"
STATE_BENCH_PROTOCOL_ID: Final = "state_bench_v0.8.1_gpt54"
DEFAULT_TOP_K: Final = 3
OFFICIAL_NUM_RUNS: Final = 5
DEFAULT_RECIPE_NAME: Final = "claims_hybrid_rrf"
RENDER_FORMAT_VERSION: Final = "learning-string-v1"
SOURCE_KIND: Final = "state_bench_train"

DOMAINS: Final = ("travel", "customer_support", "shopping_assistant")

ARM_KEYS: Final = (
    "empty",
    "full_context",
    "bm25",
    "dense",
    "mem0",
    "graphiti",
    "rememberstack",
)

SUB_PROTOCOLS: Final = ("shared", "native")

# Arms without a product write path only participate in the shared sub-protocol.
ARM_SUB_PROTOCOLS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "empty": ("shared",),
        "full_context": ("shared",),
        "bm25": ("shared",),
        "dense": ("shared",),
        "mem0": ("shared", "native"),
        "graphiti": ("shared", "native"),
        # native raw-trajectory ingest is not implemented yet — shared only.
        "rememberstack": ("shared",),
    }
)

# Arms that must prepare/evaluate a single domain (one deployment / document pool).
SINGLE_DOMAIN_ARMS: Final = frozenset(
    {"rememberstack", "bm25", "dense", "mem0", "graphiti"}
)

TIERS: Final = ("smoke", "development", "publication")

TIER_TASKS_PER_DOMAIN: Final[Mapping[str, int]] = MappingProxyType(
    {"smoke": 5, "development": 15, "publication": 50}
)

# Pre-registered publication claim bar (design §kill criteria).
MIN_LIFT_PASS_AT_1: Final = 0.08
MIN_FULL_CONTEXT_LIFT: Final = 0.05


def source_ref(*, domain: str, task_id: str) -> str:
    """Stable public ingest source_ref for one train trajectory."""
    return f"{domain}/{task_id}"


def learning_string(
    *, source_id: str, text: str, rank: int, extra: str | None = None
) -> str:
    """Canonical three-string envelope item for retrieve_learnings."""
    body = text.strip()
    if extra:
        body = f"{body}\n{extra.strip()}"
    return f"[rank={rank} source={source_id}]\n{body}"
