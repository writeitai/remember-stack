"""D61/D126 substrate seam for System One model evaluations."""

from typing import Any
from typing import Mapping
from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import ProviderCallUsage


@runtime_checkable
class SystemOnePort(Protocol):
    """Substrate seam for System One model evaluations (D61/D126)."""

    def evaluate(
        self,
        *,
        model: str,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        timeout_s: float | None = None,
    ) -> tuple[Mapping[str, Any], ProviderCallUsage]:
        """Evaluate a state dictionary against a map of typed questions."""
        ...
