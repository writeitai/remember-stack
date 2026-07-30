"""RS-Mem2Act-v1 constants."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final
from typing import Mapping

PROTOCOL_NAME: Final = "RS-Mem2Act-v1"
ADAPTER_VERSION: Final = "mem2act-adapter-2026.07"
DATASET_COMMIT: Final = "b00726940b5abbe9bd324bdd7a2cb272f5c62a29"
DATASET_SUBDIR: Final = "Mem2ActBench"
DEFAULT_TOP_K: Final = 8
DEFAULT_RECIPE_NAME: Final = "claims_hybrid_rrf"
SOURCE_KIND: Final = "mem2act_session"
RENDER_FORMAT_VERSION: Final = "mem2act-learning-v1"

TIERS: Final = ("smoke", "development", "publication")
ARM_KEYS: Final = ("empty", "rememberstack", "full_context")

TIER_COUNTS: Final[Mapping[str, int]] = MappingProxyType(
    {"smoke": 12, "development": 40, "publication": 323}
)
