"""Per-tier resource limits for the open query space (design §4.3, as amended).

Two tiers exist: interactive (the default pool) and analytical (a separate
operator-entitled one-concurrent-query pool). Each request runs under the
tier's defaults; a caller may raise a value only up to the tier's hard cap.
The planner-cost admission gate and per-aggregate input-cardinality caps were
deliberately removed (operator measure-first directive, 2026-08-04): timeouts,
memory, and temp caps bound the damage.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class LimitTier(StrEnum):
    """Which §4.3 column governs a request."""

    INTERACTIVE = "interactive"
    ANALYTICAL = "analytical"


@dataclass(frozen=True)
class TierLimits:
    """One tier's default and hard values, exactly as bound in §4.3."""

    statement_timeout_ms_default: int
    statement_timeout_ms_hard: int
    lock_timeout_ms: int
    idle_transaction_ms: int
    returned_rows_default: int
    returned_rows_hard: int
    returned_bytes_default: int
    returned_bytes_hard: int
    work_mem_kib: int
    temp_file_kib: int
    sql_text_bytes: int
    parameters_max: int
    parameters_bytes: int
    recursive_ctes_max: int
    recursion_depth_hard: int
    concurrent_per_principal: int
    concurrent_per_deployment: int
    principal_statement_seconds_per_minute: int
    deployment_statement_seconds_per_minute: int


INTERACTIVE_LIMITS: Final = TierLimits(
    statement_timeout_ms_default=5_000,
    statement_timeout_ms_hard=15_000,
    lock_timeout_ms=250,
    idle_transaction_ms=5_000,
    returned_rows_default=200,
    returned_rows_hard=1_000,
    returned_bytes_default=1_048_576,
    returned_bytes_hard=8_388_608,
    work_mem_kib=16_384,
    temp_file_kib=65_536,
    sql_text_bytes=65_536,
    parameters_max=64,
    parameters_bytes=262_144,
    recursive_ctes_max=1,
    recursion_depth_hard=6,
    concurrent_per_principal=2,
    concurrent_per_deployment=8,
    principal_statement_seconds_per_minute=30,
    deployment_statement_seconds_per_minute=120,
)

ANALYTICAL_LIMITS: Final = TierLimits(
    statement_timeout_ms_default=60_000,
    statement_timeout_ms_hard=60_000,
    lock_timeout_ms=2_000,
    idle_transaction_ms=15_000,
    returned_rows_default=10_000,
    returned_rows_hard=10_000,
    returned_bytes_default=67_108_864,
    returned_bytes_hard=67_108_864,
    work_mem_kib=65_536,
    temp_file_kib=65_536,
    sql_text_bytes=65_536,
    parameters_max=256,
    parameters_bytes=1_048_576,
    recursive_ctes_max=1,
    recursion_depth_hard=6,
    concurrent_per_principal=1,
    concurrent_per_deployment=4,
    principal_statement_seconds_per_minute=60,
    deployment_statement_seconds_per_minute=240,
)

TIER_LIMITS: Final = {
    LimitTier.INTERACTIVE: INTERACTIVE_LIMITS,
    LimitTier.ANALYTICAL: ANALYTICAL_LIMITS,
}


def clamp_rows(*, tier: TierLimits, requested: int | None) -> int:
    """The effective row cap: the default, raised at most to the hard cap."""
    if requested is None:
        return tier.returned_rows_default
    return max(1, min(requested, tier.returned_rows_hard))


def clamp_bytes(*, tier: TierLimits, requested: int | None) -> int:
    """The effective returned-byte cap: the default, raised at most to the hard cap."""
    if requested is None:
        return tier.returned_bytes_default
    return max(1, min(requested, tier.returned_bytes_hard))


def clamp_timeout_ms(*, tier: TierLimits, requested: int | None) -> int:
    """The effective statement timeout: the default, raised at most to the hard cap."""
    if requested is None:
        return tier.statement_timeout_ms_default
    return max(1, min(requested, tier.statement_timeout_ms_hard))
