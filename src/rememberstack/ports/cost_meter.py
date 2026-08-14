"""Provider-neutral sink for attributing one worker attempt's model calls."""

from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import ProviderCallUsage


@runtime_checkable
class CostMeterPort(Protocol):
    """Record provider usage under a deterministic call key and cascade tier."""

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        """Persist one provider call for the bound processing attempt.

        ``outcome`` is written by the caller. Usage-bearing provider failures
        must pass ``provider_error``; do not reconstruct it from ``tier``.
        """
        ...
