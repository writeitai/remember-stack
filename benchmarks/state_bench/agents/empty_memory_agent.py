"""STATE-Bench empty-memory arm: tool present, always returns []."""

# pyright: reportMissingImports=false
# Installed into the pinned STATE-Bench agents/ tree at cell run time.

from __future__ import annotations

from state_bench.agents.state_bench import StateBenchAgent


class EmptyMemoryAgent(StateBenchAgent):
    """No-memory control that still exposes retrieve_learnings."""

    def retrieve_learnings(self, query: str, top_k: int = 3) -> list[str]:
        """Return no learnings (matched empty arm)."""
        del query, top_k
        return []
