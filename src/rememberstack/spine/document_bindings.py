"""D102 document-local exact-name binding projection and setup rebuild."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import text
from sqlalchemy.engine import Engine

DOCUMENT_BINDING_GENERATION: Final = "document-t0-v1"
"""The exact feature/projection contract understood by the resolver."""

DOCUMENT_BINDING_REBUILD_PAGE_SIZE: Final = 1_000
"""Maximum resolution decisions read in one setup transaction."""


class DocumentBindingRebuildError(RuntimeError):
    """Historical binding state cannot be verified, so replay stays disabled."""


@dataclass(frozen=True)
class _RebuildDecision:
    """One historical decision and the document-local coordinates it implies."""

    decision_id: UUID
    decided_at: datetime
    doc_id: UUID
    entity_id: UUID
    method: str
    is_new_entity: bool
    features: dict[str, object] | None


class DocumentBindingRebuilder:
    """Rebuild D102's derived projection before enabling document-local T0."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the idempotent setup repair to one deployment database."""
        self._engine = engine

    def rebuild_if_needed(self, *, deployment_id: UUID) -> bool:
        """Build missing bindings in bounded pages and enable the generation.

        Returns true only when a rebuild was necessary. New deployments already
        carry the current generation and skip this historical repair.
        """
        with self._engine.begin() as connection:
            generation = connection.execute(
                _LOCK_GENERATION, {"deployment_id": deployment_id}
            ).scalar_one()
            if generation == DOCUMENT_BINDING_GENERATION:
                return False
            connection.execute(_DISABLE_GENERATION, {"deployment_id": deployment_id})

        cursor_decided_at: datetime | None = None
        cursor_decision_id: UUID | None = None
        while True:
            with self._engine.begin() as connection:
                rows = tuple(
                    _RebuildDecision(
                        decision_id=row["decision_id"],
                        decided_at=row["decided_at"],
                        doc_id=row["doc_id"],
                        entity_id=row["entity_id"],
                        method=str(row["method"]),
                        is_new_entity=bool(row["is_new_entity"]),
                        features=row["features"],
                    )
                    for row in connection.execute(
                        _DECISION_PAGE,
                        {
                            "deployment_id": deployment_id,
                            "cursor_decided_at": cursor_decided_at,
                            "cursor_decision_id": cursor_decision_id,
                            "limit": DOCUMENT_BINDING_REBUILD_PAGE_SIZE,
                        },
                    ).mappings()
                )
                if not rows:
                    break
                legacy_entity_ids = tuple(
                    dict.fromkeys(
                        row.entity_id
                        for row in rows
                        if _document_coordinate(features=row.features) is None
                    )
                )
                aliases: dict[UUID, tuple[str, ...]] = {}
                if legacy_entity_ids:
                    mutable_aliases: dict[UUID, list[str]] = {
                        entity_id: [] for entity_id in legacy_entity_ids
                    }
                    for alias in connection.execute(
                        _CANONICAL_ALIASES,
                        {
                            "deployment_id": deployment_id,
                            "entity_ids": list(legacy_entity_ids),
                        },
                    ).mappings():
                        mutable_aliases[alias["entity_id"]].append(
                            str(alias["normalized_lemma"])
                        )
                    aliases = {
                        entity_id: tuple(lemmas)
                        for entity_id, lemmas in mutable_aliases.items()
                    }
                binding_rows = _binding_rows(
                    deployment_id=deployment_id, decisions=rows, aliases=aliases
                )
                if binding_rows:
                    connection.execute(_UPSERT_BINDING, binding_rows)
                cursor_decided_at = rows[-1].decided_at
                cursor_decision_id = rows[-1].decision_id

        if not self._verify_complete(deployment_id=deployment_id):
            raise DocumentBindingRebuildError(
                "document binding rebuild could not represent every live document "
                "resolution decision"
            )
        with self._engine.begin() as connection:
            connection.execute(
                _ENABLE_GENERATION,
                {
                    "deployment_id": deployment_id,
                    "generation": DOCUMENT_BINDING_GENERATION,
                },
            )
        return True

    def _verify_complete(self, *, deployment_id: UUID) -> bool:
        """Verify historical membership in bounded keyset pages."""
        cursor_decided_at: datetime | None = None
        cursor_decision_id: UUID | None = None
        while True:
            with self._engine.connect() as connection:
                rows = tuple(
                    connection.execute(
                        _VERIFICATION_PAGE,
                        {
                            "deployment_id": deployment_id,
                            "cursor_decided_at": cursor_decided_at,
                            "cursor_decision_id": cursor_decision_id,
                            "limit": DOCUMENT_BINDING_REBUILD_PAGE_SIZE,
                        },
                    ).mappings()
                )
            if not rows:
                return True
            if any(bool(row["missing"]) for row in rows):
                return False
            cursor_decided_at = rows[-1]["decided_at"]
            cursor_decision_id = rows[-1]["decision_id"]


def _document_coordinate(
    *, features: dict[str, object] | None
) -> tuple[UUID, str] | None:
    """Return a validated D102 coordinate or identify a legacy decision."""
    if not isinstance(features, dict):
        return None
    raw = features.get("document_t0")
    if not isinstance(raw, dict) or raw.get("contract") != DOCUMENT_BINDING_GENERATION:
        return None
    raw_doc_id = raw.get("doc_id")
    lemma = raw.get("canonical_lemma")
    if not isinstance(raw_doc_id, str) or not isinstance(lemma, str) or not lemma:
        raise DocumentBindingRebuildError(
            "malformed document-t0-v1 resolution decision feature"
        )
    try:
        doc_id = UUID(raw_doc_id)
    except ValueError as error:
        raise DocumentBindingRebuildError(
            "document-t0-v1 resolution decision has an invalid doc_id"
        ) from error
    return doc_id, lemma


def _binding_rows(
    *,
    deployment_id: UUID,
    decisions: tuple[_RebuildDecision, ...],
    aliases: dict[UUID, tuple[str, ...]],
) -> list[dict[str, object]]:
    """Expand exact D102 rows and conservative pre-D102 canonical aliases."""
    by_key: dict[tuple[UUID, str, UUID], dict[str, object]] = {}
    for decision in decisions:
        coordinate = _document_coordinate(features=decision.features)
        if coordinate is not None:
            doc_id, lemma = coordinate
            if doc_id != decision.doc_id:
                raise DocumentBindingRebuildError(
                    "document-t0-v1 decision doc_id disagrees with mention"
                )
            lemmas = (lemma,)
            is_anchor = decision.method == "T4_small" and not decision.is_new_entity
        else:
            lemmas = aliases.get(decision.entity_id, ())
            is_anchor = False
        for lemma in lemmas:
            key = (decision.doc_id, lemma, decision.entity_id)
            current = by_key.get(key)
            if current is None:
                current = {
                    "deployment_id": deployment_id,
                    "doc_id": decision.doc_id,
                    "canonical_lemma": lemma,
                    "entity_id": decision.entity_id,
                    "anchor_decision_id": None,
                    "anchor_decided_at": None,
                }
                by_key[key] = current
            if is_anchor:
                current["anchor_decision_id"] = decision.decision_id
                current["anchor_decided_at"] = decision.decided_at
    return list(by_key.values())


_LOCK_GENERATION = text(
    """
    SELECT document_binding_generation
    FROM deployments
    WHERE deployment_id = :deployment_id
    FOR UPDATE
    """
)

_DISABLE_GENERATION = text(
    """
    UPDATE deployments SET document_binding_generation = NULL
    WHERE deployment_id = :deployment_id
    """
)

_DECISION_PAGE = text(
    """
    SELECT decision.decision_id, decision.decided_at, mention.doc_id,
           decision.entity_id, decision.method::text AS method,
           decision.is_new_entity, decision.features
    FROM resolution_decisions decision
    JOIN mentions mention
      ON mention.deployment_id = decision.deployment_id
     AND mention.mention_id = decision.mention_id
    JOIN documents document
      ON document.deployment_id = mention.deployment_id
     AND document.doc_id = mention.doc_id
     AND document.deleted_at IS NULL
    WHERE decision.deployment_id = :deployment_id
      AND (
        CAST(:cursor_decided_at AS timestamptz) IS NULL
        OR (decision.decided_at, decision.decision_id)
           > (:cursor_decided_at, :cursor_decision_id)
      )
    ORDER BY decision.decided_at, decision.decision_id
    LIMIT :limit
    """
)

_CANONICAL_ALIASES = text(
    """
    SELECT entity_id, normalized_lemma
    FROM aliases
    WHERE deployment_id = :deployment_id
      AND entity_id IN :entity_ids
      AND provenance = 'llm_canonical'
    ORDER BY entity_id, normalized_lemma
    """
).bindparams(bindparam("entity_ids", expanding=True))

_UPSERT_BINDING = text(
    """
    INSERT INTO document_entity_bindings (
        deployment_id, doc_id, canonical_lemma, entity_id,
        anchor_decision_id, anchor_decided_at
    ) VALUES (
        :deployment_id, :doc_id, :canonical_lemma, :entity_id,
        :anchor_decision_id, :anchor_decided_at
    )
    ON CONFLICT (deployment_id, doc_id, canonical_lemma, entity_id)
    DO UPDATE SET
      anchor_decision_id = CASE
        WHEN EXCLUDED.anchor_decision_id IS NOT NULL
        THEN EXCLUDED.anchor_decision_id
        ELSE document_entity_bindings.anchor_decision_id
      END,
      anchor_decided_at = CASE
        WHEN EXCLUDED.anchor_decision_id IS NOT NULL
        THEN EXCLUDED.anchor_decided_at
        ELSE document_entity_bindings.anchor_decided_at
      END
    """
).bindparams(bindparam("anchor_decision_id"), bindparam("anchor_decided_at"))

_VERIFICATION_PAGE = text(
    """
    SELECT decision.decision_id, decision.decided_at,
           NOT EXISTS (
             SELECT 1
             FROM document_entity_bindings binding
             WHERE binding.deployment_id = decision.deployment_id
               AND binding.doc_id = mention.doc_id
               AND binding.entity_id = decision.entity_id
           ) AS missing
    FROM resolution_decisions decision
    JOIN mentions mention
      ON mention.deployment_id = decision.deployment_id
     AND mention.mention_id = decision.mention_id
    JOIN documents document
      ON document.deployment_id = mention.deployment_id
     AND document.doc_id = mention.doc_id
     AND document.deleted_at IS NULL
    WHERE decision.deployment_id = :deployment_id
      AND (
        CAST(:cursor_decided_at AS timestamptz) IS NULL
        OR (decision.decided_at, decision.decision_id)
           > (:cursor_decided_at, :cursor_decision_id)
      )
    ORDER BY decision.decided_at, decision.decision_id
    LIMIT :limit
    """
)

_ENABLE_GENERATION = text(
    """
    UPDATE deployments
    SET document_binding_generation = :generation
    WHERE deployment_id = :deployment_id
    """
)
