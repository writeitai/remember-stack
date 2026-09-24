"""The persisted last accepted revocation document (D136 §7.5).

One row per deployment. It exists so a restart cannot roll revocation back:
the engine loads it at start-up, and a newly fetched document is accepted only
with a strictly greater ``seq``. The write is conditional on the stored
``seq`` being lower, so several API replicas sharing the row can only move it
forward.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

_LOAD = text(
    "SELECT document FROM perimeter_state WHERE deployment_id = :deployment_id"
)

_SAVE = text(
    """
    INSERT INTO perimeter_state (deployment_id, seq, document)
    VALUES (:deployment_id, :seq, :document)
    ON CONFLICT (deployment_id) DO UPDATE
      SET seq = EXCLUDED.seq, document = EXCLUDED.document, accepted_at = now()
      WHERE perimeter_state.seq < EXCLUDED.seq
    RETURNING deployment_id
    """
).bindparams(bindparam("document", type_=JSONB))


class PerimeterStateCatalog:
    """Load and conditionally advance the accepted revocation document."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind to the spine engine."""
        self._engine = engine

    def load(self, *, deployment_id: UUID) -> Mapping[str, Any] | None:
        """The accepted document's claims, or ``None`` before the first one."""
        with self._engine.connect() as connection:
            document = connection.execute(
                _LOAD, {"deployment_id": deployment_id}
            ).scalar_one_or_none()
        return document

    def save(
        self, *, deployment_id: UUID, seq: int, document: Mapping[str, Any]
    ) -> bool:
        """Store the document if ``seq`` is greater than the stored one."""
        with self._engine.begin() as connection:
            stored = connection.execute(
                _SAVE,
                {
                    "deployment_id": deployment_id,
                    "seq": seq,
                    "document": dict(document),
                },
            ).scalar_one_or_none()
        return stored is not None
