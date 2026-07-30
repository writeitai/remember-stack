"""STATE-Bench BM25 floor over shared serialized train documents."""

# pyright: reportMissingImports=false
# Installed into the pinned STATE-Bench agents/ tree at cell run time.

from __future__ import annotations

import json
from typing import Any

from state_bench.agents.state_bench import StateBenchAgent

from benchmarks.state_bench.model import TrajectoryDocument
from benchmarks.state_bench.settings import Bm25ArmSettings
from benchmarks.state_bench.trajectories import bm25_learning_strings


class Bm25MemoryAgent(StateBenchAgent):
    """Lexical floor: ranks prepared domain-scoped documents without neural models.

    Requires ``RS_STATE_DOCUMENTS_JSON`` (and optionally ``RS_STATE_DOMAIN``).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._documents: tuple[TrajectoryDocument, ...] | None = None
        self._settings = Bm25ArmSettings.model_validate({})

    def retrieve_learnings(self, query: str, top_k: int = 3) -> list[str]:
        """Return top_k lexical matches over the domain-scoped document set."""
        documents = self._load_documents()
        return bm25_learning_strings(documents=documents, query=query, top_k=top_k)

    def _load_documents(self) -> tuple[TrajectoryDocument, ...]:
        if self._documents is not None:
            return self._documents
        path = self._settings.documents_json
        raw = json.loads(path.read_text(encoding="utf-8"))
        documents = tuple(TrajectoryDocument.model_validate(item) for item in raw)
        if self._settings.domain is not None:
            documents = tuple(
                document
                for document in documents
                if document.domain == self._settings.domain
            )
        self._documents = documents
        return self._documents
