"""A no-op cost meter for tests that invoke stage handlers directly."""

from rememberstack.model import ProviderCallUsage


class NoopCostMeter:
    """Accept provider accounting without persisting it."""

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        """Discard one test-only call record."""
        del call_key, tier, usage, outcome
