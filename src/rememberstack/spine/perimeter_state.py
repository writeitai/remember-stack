"""The persisted last accepted revocation document (D136 §7.5).

One row per deployment. It exists so a restart cannot roll revocation back:
the engine loads it at start-up, and a newly fetched document is accepted only
with a strictly greater ``seq``. The write is a compare-and-set on the
``seq`` the caller verified against, so several API replicas sharing the row
can only move it forward, and never past a document they have not seen.
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

_INSERT_FIRST = text(
    """
    INSERT INTO perimeter_state (deployment_id, seq, document)
    VALUES (:deployment_id, :seq, :document)
    ON CONFLICT (deployment_id) DO NOTHING
    RETURNING deployment_id
    """
).bindparams(bindparam("document", type_=JSONB))

_REPLACE_EXPECTED = text(
    """
    UPDATE perimeter_state
       SET seq = :seq, document = :document, accepted_at = now()
     WHERE deployment_id = :deployment_id AND seq = :expected_seq AND seq < :seq
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
        self,
        *,
        deployment_id: UUID,
        expected_seq: int | None,
        seq: int,
        document: Mapping[str, Any],
    ) -> bool:
        """Replace the row only if its ``seq`` is still ``expected_seq`` (None: no row)."""
        parameters = {
            "deployment_id": deployment_id,
            "seq": seq,
            "document": dict(document),
        }
        with self._engine.begin() as connection:
            if expected_seq is None:
                result = connection.execute(_INSERT_FIRST, parameters)
            else:
                result = connection.execute(
                    _REPLACE_EXPECTED, {**parameters, "expected_seq": expected_seq}
                )
            return result.scalar_one_or_none() is not None
