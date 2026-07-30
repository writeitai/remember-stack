"""STATE-Bench RememberStack arm: retrieve_learnings over public recipes."""

# pyright: reportMissingImports=false
# Installed into the pinned STATE-Bench agents/ tree at cell run time.

from __future__ import annotations

from typing import Any

from state_bench.agents.state_bench import StateBenchAgent

from benchmarks.state_bench.protocol import DEFAULT_TOP_K
from benchmarks.state_bench.retrieve import format_error_learning
from benchmarks.state_bench.retrieve import format_learnings_from_envelope
from benchmarks.state_bench.retrieve import format_zero_hit_learning
from benchmarks.state_bench.retrieve import RetrievalRenderError
from benchmarks.state_bench.settings import RememberStackArmSettings


class RememberStackMemoryAgent(StateBenchAgent):
    """Bounded public-recipe retrieval behind STATE's memory hook.

    Configuration via ``RS_STATE_*`` pydantic-settings (not ``os.environ``).
    Infrastructure failures are fail-closed when ``RS_STATE_FAIL_CLOSED=true``
    (default): they re-raise so the cell does not silently score as empty.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client = None
        self._settings = RememberStackArmSettings.model_validate({})
        self.retrieve_calls = 0
        self.retrieve_errors = 0
        self.retrieve_empty = 0

    def retrieve_learnings(self, query: str, top_k: int = 3) -> list[str]:
        """Query the ordinary RememberStack deployment; return ≤ top_k strings."""
        resolved_k = top_k if top_k >= 1 else DEFAULT_TOP_K
        self.retrieve_calls += 1
        try:
            client = self._memory_client()
            envelope = client.run_recipe(
                name=self._settings.recipe, arguments={"query": query, "k": resolved_k}
            )
            items = format_learnings_from_envelope(envelope, top_k=resolved_k)
            if not items:
                self.retrieve_empty += 1
                return format_zero_hit_learning(top_k=resolved_k)
            return items
        except RetrievalRenderError:
            self.retrieve_errors += 1
            raise
        except Exception as error:
            self.retrieve_errors += 1
            if self._settings.fail_closed:
                raise
            return format_error_learning(
                error_class=type(error).__name__, top_k=resolved_k
            )

    def _memory_client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            from rememberstack.surfaces.sdk import MemoryClient

            self._client = MemoryClient.from_settings()
        return self._client
