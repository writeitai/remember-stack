"""Frozen source-owned Selection results shared with later Claimify work."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import Connection
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core.selection_references import card_from_payload
from rememberstack.core.selection_references import card_payload
from rememberstack.core.selection_references import CardDiagnostic
from rememberstack.core.selection_references import GroundedCard
from rememberstack.model import ForgetInProgressError
from rememberstack.model import SelectionResponse
from rememberstack.spine.admission import active_forget_id_on


@dataclass(frozen=True)
class FrozenSelection:
    """The committed response and grounded cards for one source occurrence."""

    chunk_id: UUID
    input_hash: str
    selection: SelectionResponse
    cards: tuple[GroundedCard, ...]
    diagnostics: tuple[CardDiagnostic, ...]
    truncated: bool


def require_extraction_sources_on(
    *, connection: Connection, deployment_id: UUID, chunk_ids: tuple[UUID, ...]
) -> None:
    """Fence late publication against forget and require every source to survive."""
    connection.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
        {"key": f"hard-forget:{deployment_id}"},
    )
    if (
        active_forget_id_on(connection=connection, deployment_id=deployment_id)
        is not None
    ):
        raise ForgetInProgressError("extraction publication is fenced by hard forget")
    present = set(
        connection.execute(
            text("""
        SELECT c.chunk_id FROM chunks c JOIN documents d
          ON d.deployment_id=c.deployment_id AND d.doc_id=c.doc_id
        JOIN document_versions v ON v.version_id=c.version_id
        JOIN document_representations r ON r.representation_id=c.representation_id
        WHERE c.deployment_id=:deployment_id
          AND c.chunk_id=ANY(CAST(:chunk_ids AS uuid[])) AND d.deleted_at IS NULL
          -- D135: a deleted VERSION of a live (re-added) lineage publishes
          -- nothing either (the lock on d already serializes with a delete)
          AND v.deleted_at IS NULL
        FOR SHARE OF d, r
    """),
            {"deployment_id": deployment_id, "chunk_ids": list(chunk_ids)},
        ).scalars()
    )
    if present != set(chunk_ids):
        raise LookupError("extraction source disappeared before publication")


class SelectionCatalog:
    """One committed Selection result per chunk and extraction generation."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the source-result store to the deployment database."""
        self._engine = engine

    def load(self, *, chunk_id: UUID, extractor_version: str) -> FrozenSelection | None:
        """Load the authoritative result, including an explicitly empty response."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT * FROM selection_results
                WHERE chunk_id=:chunk_id AND extractor_version=:extractor_version
            """),
                    {"chunk_id": chunk_id, "extractor_version": extractor_version},
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _decode(row=dict(row))

    def preceding(
        self, *, chunk_ids: tuple[UUID, ...], extractor_version: str
    ) -> dict[UUID, FrozenSelection]:
        """Load a bounded known set; missing results remain missing, never empty."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT * FROM selection_results
                WHERE chunk_id=ANY(CAST(:chunk_ids AS uuid[]))
                  AND extractor_version=:extractor_version
            """),
                    {
                        "chunk_ids": list(chunk_ids),
                        "extractor_version": extractor_version,
                    },
                )
                .mappings()
                .all()
            )
        results = tuple(_decode(row=dict(row)) for row in rows)
        return {result.chunk_id: result for result in results}

    def prior(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        input_hash: str,
        extractor_version: str,
    ) -> FrozenSelection | None:
        """Reuse only an earlier version of this document with identical inputs."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT s.* FROM selection_results s JOIN chunks c
                  ON c.chunk_id=s.chunk_id AND c.deployment_id=s.deployment_id
                JOIN document_versions v ON v.version_id=c.version_id
                WHERE s.deployment_id=:deployment_id AND c.doc_id=:doc_id
                  AND s.selection_input_hash=:input_hash
                  AND s.extractor_version=:extractor_version
                  AND v.version_no < (SELECT version_no FROM document_versions
                                       WHERE version_id=:version_id)
                  AND v.deleted_at IS NULL
                ORDER BY v.version_no DESC,c.ordinal LIMIT 1
            """),
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "version_id": version_id,
                        "input_hash": input_hash,
                        "extractor_version": extractor_version,
                    },
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _decode(row=dict(row))

    def freeze(
        self,
        *,
        deployment_id: UUID,
        representation_id: UUID,
        chunk_id: UUID,
        extractor_version: str,
        input_hash: str,
        selection: SelectionResponse,
        cards: tuple[GroundedCard, ...],
        diagnostics: tuple[CardDiagnostic, ...],
        truncated: bool,
        reused_from: UUID | None = None,
    ) -> FrozenSelection:
        """Publish once under forget coordination; concurrent losers read the winner."""
        payload = {
            "selection": selection.model_dump(mode="json"),
            "diagnostics": [
                {"ordinal": d.ordinal, "gate": d.gate, "detail": d.detail}
                for d in diagnostics
            ],
        }
        with self._engine.begin() as connection:
            require_extraction_sources_on(
                connection=connection,
                deployment_id=deployment_id,
                chunk_ids=(chunk_id,)
                if reused_from is None
                else (chunk_id, reused_from),
            )
            connection.execute(
                _INSERT,
                {
                    "deployment_id": deployment_id,
                    "representation_id": representation_id,
                    "chunk_id": chunk_id,
                    "extractor_version": extractor_version,
                    "input_hash": input_hash,
                    "output": payload,
                    "cards": [card_payload(card=card) for card in cards],
                    "truncated": truncated,
                },
            )
            row = (
                connection.execute(
                    text("""
                SELECT * FROM selection_results WHERE deployment_id=:deployment_id
                  AND chunk_id=:chunk_id AND extractor_version=:extractor_version
            """),
                    {
                        "deployment_id": deployment_id,
                        "chunk_id": chunk_id,
                        "extractor_version": extractor_version,
                    },
                )
                .mappings()
                .one()
            )
        result = _decode(row=dict(row))
        if result.input_hash != input_hash:
            raise ValueError("committed Selection input identity differs")
        return result


def _decode(*, row: dict[str, object]) -> FrozenSelection:
    """Validate persisted values before they can become another model's input."""
    output = row["output"]
    cards = row["cards"]
    if not isinstance(output, dict) or not isinstance(cards, list):
        raise ValueError("invalid frozen Selection payload")
    diagnostics = output.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        raise ValueError("invalid frozen Selection diagnostics")
    return FrozenSelection(
        chunk_id=UUID(str(row["chunk_id"])),
        input_hash=str(row["selection_input_hash"]),
        selection=SelectionResponse.model_validate(output["selection"]),
        cards=tuple(card_from_payload(payload=card) for card in cards),
        diagnostics=tuple(
            CardDiagnostic(
                ordinal=int(d["ordinal"]), gate=str(d["gate"]), detail=str(d["detail"])
            )
            for d in diagnostics
        ),
        truncated=bool(row["truncated"]),
    )


_INSERT = text("""
    INSERT INTO selection_results
      (deployment_id,chunk_id,representation_id,extractor_version,selection_input_hash,output,cards,truncated)
    VALUES (:deployment_id,:chunk_id,:representation_id,:extractor_version,:input_hash,:output,:cards,:truncated)
    ON CONFLICT (deployment_id,chunk_id,extractor_version) DO NOTHING
""").bindparams(bindparam("output", type_=JSON), bindparam("cards", type_=JSON))
