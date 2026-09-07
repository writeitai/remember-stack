"""Atomic publication of complete claim normalization before any fact attachment."""

from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core.fact_temporal import fact_kind
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.normalization import NormalizationInput
from rememberstack.model.normalization import NormalizationOutput
from rememberstack.model.normalization import NormalizationReceipt
from rememberstack.model.normalization import NormalizedObservation
from rememberstack.model.normalization import NormalizedRelation
from rememberstack.model.relations import ClaimForNormalization
from rememberstack.spine.temporal_journal import _canonical_subject
from rememberstack.spine.temporal_journal import _utc_timestamp
from rememberstack.spine.temporal_journal import temporal_fingerprint
from rememberstack.spine.temporal_journal import temporal_identity_admission
from rememberstack.spine.temporal_journal import TemporalWriteConflict


class NormalizationCatalog:
    """Publish one source-bearing receipt and every distinct relation assertion atomically."""

    def __init__(self, *, engine: Engine) -> None:
        """Use the existing spine transaction and generation/forget admission protocol."""
        self._engine = engine

    def input_snapshot(
        self, *, deployment_id: UUID, claim_id: UUID
    ) -> NormalizationInput:
        """Load exact immutable source inputs before any remote normalization or resolution."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=deployment_id
            ),
        ):
            return _input_on(
                connection=connection, deployment_id=deployment_id, claim_id=claim_id
            )

    def receipt(
        self, *, deployment_id: UUID, claim_id: UUID, normalizer_version: str
    ) -> NormalizationReceipt | None:
        """Reuse a complete output, including empty outcomes, without repeating inference."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=deployment_id
            ),
        ):
            return _receipt_on(
                connection=connection,
                deployment_id=deployment_id,
                claim_id=claim_id,
                normalizer_version=normalizer_version,
            )

    def publish(
        self,
        *,
        prepared: NormalizationInput,
        normalizer_version: str,
        output: NormalizationOutput,
    ) -> NormalizationReceipt:
        """Revalidate source/identity, then let the first complete publication win.

        Relations remain assertions and observations remain in the complete
        JSON output. Version barriers later materialize their exact membership;
        no relation/observation fact or evidence attachment is written here.
        """
        if not normalizer_version:
            raise ValueError("normalizer_version must be nonempty")
        deployment_id = prepared.claim.deployment_id
        claim_id = prepared.claim.claim_id
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=deployment_id
            ),
        ):
            existing = _receipt_on(
                connection=connection,
                deployment_id=deployment_id,
                claim_id=claim_id,
                normalizer_version=normalizer_version,
            )
            if existing is not None:
                return existing
            current = _input_on(
                connection=connection, deployment_id=deployment_id, claim_id=claim_id
            )
            if current != prepared:
                raise TemporalWriteConflict(
                    "normalization source changed before publication"
                )
            canonical = _canonical_output_on(
                connection=connection, prepared=current, output=output
            )
            receipt_id = uuid4()
            inserted = connection.execute(
                _INSERT_RECEIPT,
                {
                    "id": receipt_id,
                    "dep": deployment_id,
                    "claim": claim_id,
                    "doc": current.claim.doc_id,
                    "version": normalizer_version,
                    "outcome": canonical.outcome,
                    "input": current.input_digest,
                    "output_digest": temporal_fingerprint(
                        value=canonical.model_dump(mode="json")
                    ),
                    "output": canonical.model_dump_json(),
                    "relations": len(canonical.relations),
                    "observations": len(canonical.observations),
                },
            ).scalar_one_or_none()
            if inserted is not None and canonical.relations:
                connection.execute(
                    _INSERT_ASSERTION,
                    [
                        {
                            "id": _assertion_id(
                                receipt_id=receipt_id, relation=relation
                            ),
                            "dep": deployment_id,
                            "receipt": receipt_id,
                            "version": normalizer_version,
                            "subject": relation.subject_entity_id,
                            "predicate": relation.predicate,
                            "object": relation.object_entity_id,
                            "shape": relation.shape_kind.value,
                        }
                        for relation in canonical.relations
                    ],
                )
            # ON CONFLICT waits for the competing complete transaction. Its
            # committed output wins even when that helper chose different IDs.
            published = _receipt_on(
                connection=connection,
                deployment_id=deployment_id,
                claim_id=claim_id,
                normalizer_version=normalizer_version,
            )
            if published is None:
                raise TemporalWriteConflict("normalization publication disappeared")
            return published


def _input_on(
    *, connection: Connection, deployment_id: UUID, claim_id: UUID
) -> NormalizationInput:
    """Fingerprint immutable claim inputs; testimony currency is rechecked at fact application."""
    row = (
        connection.execute(_SOURCE_INPUT, {"dep": deployment_id, "claim": claim_id})
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise TemporalWriteConflict("normalization source claim is missing")
    claim_fields = {name: row[name] for name in ClaimForNormalization.model_fields}
    return NormalizationInput(
        claim=ClaimForNormalization.model_validate(claim_fields),
        temporal_window=ClaimTemporalWindow.model_validate(
            {
                "claim_id": claim_id,
                "kind": row["claim_valid_kind"],
                "valid_from": _utc_timestamp(value=row["claim_valid_from"]),
                "valid_until": _utc_timestamp(value=row["claim_valid_until"]),
                "precision": row["claim_valid_precision"],
            }
        ),
        input_digest=temporal_fingerprint(value=dict(row)),
    )


def _canonical_output_on(
    *, connection: Connection, prepared: NormalizationInput, output: NormalizationOutput
) -> NormalizationOutput:
    """Resolve redirects under the held identity epoch and validate published predicate keys."""
    deployment_id = prepared.claim.deployment_id
    identities = {relation.subject_entity_id for relation in output.relations}
    identities.update(relation.object_entity_id for relation in output.relations)
    identities.update(
        observation.subject_entity_id for observation in output.observations
    )
    canonical = {
        identity: _canonical_subject(
            connection=connection, deployment_id=deployment_id, entity_id=identity
        )
        for identity in sorted(identities)
    }
    for predicate in sorted({relation.predicate for relation in output.relations}):
        if (
            connection.execute(
                text("""
            SELECT 1 FROM predicates WHERE deployment_id = :dep AND predicate = :predicate AND status = 'active'
        """),
                {"dep": deployment_id, "predicate": predicate},
            ).scalar_one_or_none()
            is None
        ):
            raise TemporalWriteConflict(
                "normalized predicate is no longer active in this deployment"
            )
    return NormalizationOutput(
        outcome=output.outcome,
        relations=tuple(
            NormalizedRelation(
                subject_entity_id=canonical[relation.subject_entity_id],
                predicate=relation.predicate,
                object_entity_id=canonical[relation.object_entity_id],
                shape_kind=fact_kind(
                    claim_kind=prepared.temporal_window.kind, shape=relation.shape_kind
                ),
            )
            for relation in output.relations
        ),
        observations=tuple(
            NormalizedObservation(
                subject_entity_id=canonical[observation.subject_entity_id],
                statement=observation.statement,
                shape_kind=fact_kind(
                    claim_kind=prepared.temporal_window.kind,
                    shape=observation.shape_kind,
                ),
            )
            for observation in output.observations
        ),
    )


def _receipt_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    claim_id: UUID,
    normalizer_version: str,
) -> NormalizationReceipt | None:
    """Validate the whole recorded output and every immutable assertion before reuse."""
    row = (
        connection.execute(
            _RECEIPT,
            {"dep": deployment_id, "claim": claim_id, "version": normalizer_version},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    receipt = _decode_receipt(row=row)
    assertions = (
        connection.execute(
            _ASSERTIONS, {"dep": deployment_id, "receipt": receipt.receipt_id}
        )
        .mappings()
        .all()
    )
    expected = {
        _assertion_id(receipt_id=receipt.receipt_id, relation=relation): relation
        for relation in receipt.output.relations
    }
    actual = {
        assertion["assertion_id"]: NormalizedRelation.model_validate(
            {name: assertion[name] for name in NormalizedRelation.model_fields}
        )
        for assertion in assertions
    }
    if actual != expected or len(assertions) != len(expected):
        raise TemporalWriteConflict(
            "normalization receipt has incomplete or changed relation assertions"
        )
    return receipt


def _decode_receipt(*, row: RowMapping) -> NormalizationReceipt:
    """Reject changed JSON/count/digest metadata rather than treating it as an empty result."""
    output = NormalizationOutput.model_validate(row["normalization_output"])
    if (
        row["outcome"] != output.outcome
        or row["relation_count"] != len(output.relations)
        or row["observation_count"] != len(output.observations)
        or row["output_digest"]
        != temporal_fingerprint(value=output.model_dump(mode="json"))
    ):
        raise TemporalWriteConflict(
            "normalization receipt output attestation disagrees"
        )
    return NormalizationReceipt(
        receipt_id=row["receipt_id"],
        deployment_id=row["deployment_id"],
        claim_id=row["claim_id"],
        doc_id=row["doc_id"],
        normalizer_version=row["normalizer_version"],
        input_digest=row["input_digest"],
        output_digest=row["output_digest"],
        output=output,
    )


def _assertion_id(*, receipt_id: UUID, relation: NormalizedRelation) -> UUID:
    """Name a distinct triple without delimiter ambiguity or dependence on model output order."""
    return uuid5(
        receipt_id,
        temporal_fingerprint(
            value=[
                relation.subject_entity_id,
                relation.predicate,
                relation.object_entity_id,
            ]
        ),
    )


_SOURCE_INPUT = text("""
    SELECT claim_id, deployment_id, doc_id, chunk_id, claim_text, is_attributed,
      extractor_version, asserted_at, claim_valid_from, claim_valid_until,
      claim_valid_precision::text, claim_valid_kind::text
    FROM claims WHERE deployment_id = :dep AND claim_id = :claim
""")
_RECEIPT = text("""
    SELECT * FROM normalize_claim_receipts WHERE deployment_id = :dep
      AND claim_id = :claim AND normalizer_version = :version
""")
_ASSERTIONS = text("""
    SELECT assertion_id, subject_entity_id, predicate, object_entity_id, shape_kind
    FROM normalize_relation_assertions WHERE deployment_id = :dep AND receipt_id = :receipt
""")
_INSERT_RECEIPT = text("""
    INSERT INTO normalize_claim_receipts (receipt_id, deployment_id, claim_id, doc_id,
      normalizer_version, outcome, input_digest, output_digest, normalization_output,
      relation_count, observation_count)
    VALUES (:id, :dep, :claim, :doc, :version, :outcome, :input, :output_digest,
            CAST(:output AS jsonb), :relations, :observations)
    ON CONFLICT (deployment_id, claim_id, normalizer_version) DO NOTHING RETURNING receipt_id
""")
_INSERT_ASSERTION = text("""
    INSERT INTO normalize_relation_assertions (assertion_id, deployment_id, receipt_id,
      normalizer_version, subject_entity_id, predicate, object_entity_id, shape_kind)
    VALUES (:id, :dep, :receipt, :version, :subject, :predicate, :object, :shape)
""")
