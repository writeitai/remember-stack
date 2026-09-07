"""D114 durable normalization and ordered fact-application preparation.

Entity units remain membership/barrier work. Only their canonical subject's
least admitted application may be prepared while inference runs without locks.
"""

from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from hashlib import sha256
import json
from typing import Any
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model.fact_application import AssertionKind
from rememberstack.model.fact_application import FactApplicationDecision
from rememberstack.model.relations import NormalizationResponse
from rememberstack.spine.admission import active_forget_id_on


class ApplicationInputChanged(RuntimeError):
    """Retry preparation after an input participant changed while acquiring locks."""


@dataclass(frozen=True)
class PreparedApplication:
    """One durable attempt; the provider must echo its identity on publication."""

    application_id: UUID
    attempt_id: UUID
    input_hash: str
    inputs: dict[str, Any]
    decision: FactApplicationDecision | None


def canonical_json(value: object) -> str:
    """Encode store values deterministically for fingerprints and JSONB payloads."""

    def encode(item: object) -> str:
        """Normalize only database UUIDs and aware instants, rejecting unknown types."""
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, datetime) and item.tzinfo is not None:
            return item.astimezone(timezone.utc).isoformat()
        raise TypeError(
            f"unsupported application snapshot value: {type(item).__name__}"
        )

    return json.dumps(
        value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@contextmanager
def application_fence(*, connection: Connection, deployment_id: UUID) -> Iterator[None]:
    """Coordinate every payload publication with the existing hard-forget fence."""
    connection.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
        {"key": f"hard-forget:{deployment_id}"},
    )
    if (
        active_forget_id_on(connection=connection, deployment_id=deployment_id)
        is not None
    ):
        raise ApplicationInputChanged("ordinary fact work is fenced by hard forget")
    yield


def canonical_entity(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> UUID:
    """Follow redirects under the identity lock and reject a broken/cyclic chain."""
    row = connection.execute(
        text("""
        WITH RECURSIVE chain AS (
          SELECT entity_id, merged_into, ARRAY[entity_id] AS path FROM entities
          WHERE deployment_id=:deployment_id AND entity_id=:entity_id
          UNION ALL
          SELECT e.entity_id,e.merged_into,c.path || e.entity_id
          FROM entities e JOIN chain c ON e.entity_id=c.merged_into
          WHERE e.deployment_id=:deployment_id AND NOT e.entity_id=ANY(c.path)
        ) SELECT entity_id FROM chain WHERE merged_into IS NULL
        """),
        {"deployment_id": deployment_id, "entity_id": entity_id},
    ).scalar_one_or_none()
    if row is None:
        raise ApplicationInputChanged("entity redirect has no surviving canonical root")
    return UUID(str(row))


def entity_members(
    *, connection: Connection, deployment_id: UUID, root: UUID
) -> list[UUID]:
    """Return the reverse redirect closure, without changing stored assertion IDs."""
    return list(
        connection.execute(
            text("""
        WITH RECURSIVE members AS (
          SELECT entity_id FROM entities WHERE deployment_id=:deployment_id AND entity_id=:root
          UNION
          SELECT e.entity_id FROM entities e JOIN members m ON e.merged_into=m.entity_id
          WHERE e.deployment_id=:deployment_id
        ) SELECT entity_id FROM members ORDER BY entity_id
        """),
            {"deployment_id": deployment_id, "root": root},
        ).scalars()
    )


@contextmanager
def application_block(
    *, connection: Connection, deployment_id: UUID, subject_entity_id: UUID
) -> Iterator[tuple[UUID, list[UUID]]]:
    """Acquire forget, identity, then canonical entity locks in their global order."""
    with application_fence(connection=connection, deployment_id=deployment_id):
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
            {"key": f"{deployment_id}:identity-epoch"},
        )
        root = canonical_entity(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=subject_entity_id,
        )
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"{deployment_id}:obs:{root}"},
        )
        yield (
            root,
            entity_members(
                connection=connection, deployment_id=deployment_id, root=root
            ),
        )


class FactApplicationCatalog:
    """The two internal stores, with no fact identity guesses during normalization."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind to the ordinary application role's database engine."""
        self._engine = engine

    def normalization(
        self, *, deployment_id: UUID, claim_id: UUID, normalizer_version: str
    ) -> tuple[NormalizationResponse, tuple[tuple[AssertionKind, int], ...]] | None:
        """Read the first complete response and its frozen original-array ordinals."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
              SELECT output,accepted_outputs FROM normalization_outputs
              WHERE deployment_id=:deployment_id AND claim_id=:claim_id
                AND normalizer_version=:normalizer_version
            """),
                    {
                        "deployment_id": deployment_id,
                        "claim_id": claim_id,
                        "normalizer_version": normalizer_version,
                    },
                )
                .mappings()
                .one_or_none()
            )
        return self._normalization_value(row=dict(row)) if row is not None else None

    @staticmethod
    def _normalization_value(
        *, row: Mapping[str, Any]
    ) -> tuple[NormalizationResponse, tuple[tuple[AssertionKind, int], ...]]:
        """Decode one immutable response; original ordinals remain unchanged."""
        return NormalizationResponse.model_validate(row["output"]), tuple(
            (entry["kind"], entry["ordinal"]) for entry in row["accepted_outputs"]
        )

    def publish_normalization(
        self,
        *,
        deployment_id: UUID,
        claim_id: UUID,
        normalizer_version: str,
        output: NormalizationResponse,
        accepted: tuple[tuple[AssertionKind, int], ...],
    ) -> tuple[NormalizationResponse, tuple[tuple[AssertionKind, int], ...]]:
        """Publish response plus dispositions once; a racing loser reads the winner."""
        if len(set(accepted)) != len(accepted):
            raise ValueError("accepted output coordinates cannot repeat")
        for kind, ordinal in accepted:
            values = output.relations if kind == "relation" else output.observations
            if (
                kind not in ("relation", "observation")
                or ordinal < 0
                or ordinal >= len(values)
            ):
                raise ValueError("accepted ordinal must address the original response")
        params = {
            "deployment_id": deployment_id,
            "claim_id": claim_id,
            "normalizer_version": normalizer_version,
            "output": output.model_dump_json(),
            "accepted": canonical_json(
                [
                    {"kind": kind, "ordinal": ordinal}
                    for kind, ordinal in sorted(accepted)
                ]
            ),
        }
        with (
            self._engine.begin() as connection,
            application_fence(connection=connection, deployment_id=deployment_id),
        ):
            connection.execute(
                text("""
              INSERT INTO normalization_outputs(deployment_id,claim_id,normalizer_version,output,accepted_outputs)
              VALUES (:deployment_id,:claim_id,:normalizer_version,CAST(:output AS jsonb),CAST(:accepted AS jsonb))
              ON CONFLICT DO NOTHING
            """),
                params,
            )
            row = (
                connection.execute(
                    text("""
              SELECT output,accepted_outputs FROM normalization_outputs
              WHERE deployment_id=:deployment_id AND claim_id=:claim_id AND normalizer_version=:normalizer_version
            """),
                    params,
                )
                .mappings()
                .one()
            )
            return self._normalization_value(row=dict(row))

    def stage(
        self,
        *,
        deployment_id: UUID,
        claim_id: UUID,
        normalizer_version: str,
        kind: AssertionKind,
        ordinal: int,
        adjudicator_version: str,
        subject_entity_id: UUID,
        object_entity_id: UUID | None,
        version_ids: tuple[UUID, ...],
    ) -> UUID:
        """Register one accepted assertion and all known source-version memberships."""
        params = {
            "deployment_id": deployment_id,
            "claim_id": claim_id,
            "normalizer_version": normalizer_version,
            "kind": kind,
            "ordinal": ordinal,
            "adjudicator_version": adjudicator_version,
            "subject": subject_entity_id,
            "object": object_entity_id,
            "application_id": uuid4(),
        }
        with (
            self._engine.begin() as connection,
            application_fence(connection=connection, deployment_id=deployment_id),
        ):
            connection.execute(
                text("""
              INSERT INTO fact_applications(application_id,deployment_id,claim_id,normalizer_version,
                output_kind,output_ordinal,adjudicator_version,subject_entity_id,object_entity_id)
              SELECT :application_id,:deployment_id,:claim_id,:normalizer_version,:kind,:ordinal,
                     :adjudicator_version,:subject,:object
              FROM normalization_outputs n WHERE n.deployment_id=:deployment_id
                AND n.claim_id=:claim_id AND n.normalizer_version=:normalizer_version
                AND EXISTS (SELECT 1 FROM jsonb_array_elements(n.accepted_outputs) x
                            WHERE x->>'kind'=:kind AND (x->>'ordinal')::int=:ordinal)
              ON CONFLICT DO NOTHING
            """),
                params,
            )
            app = (
                connection.execute(
                    text("""
              SELECT * FROM fact_applications WHERE deployment_id=:deployment_id AND claim_id=:claim_id
                AND normalizer_version=:normalizer_version AND output_kind=:kind
                AND output_ordinal=:ordinal AND adjudicator_version=:adjudicator_version
            """),
                    params,
                )
                .mappings()
                .one()
            )
            params["application_id"] = app["application_id"]
            # Already applied applications still get memberships. The barrier must see
            # their entity unit before receipt-based retirement; no support is relinked.
            for version_id in sorted(set(version_ids)):
                connection.execute(
                    text("""
                  INSERT INTO normalize_observation_staging(deployment_id,version_id,claim_id,
                    subject_entity_id,statement,doc_id,normalizer_version,application_id)
                  SELECT a.deployment_id,:version_id,a.claim_id,a.subject_entity_id,
                         CASE WHEN a.output_kind='observation' THEN
                           n.output->'observations'->a.output_ordinal->>'statement' END,
                         c.doc_id,a.normalizer_version,a.application_id
                  FROM fact_applications a JOIN claims c USING(deployment_id,claim_id)
                  JOIN normalization_outputs n USING(deployment_id,claim_id,normalizer_version)
                  WHERE a.application_id=:application_id AND a.deployment_id=:deployment_id
                  ON CONFLICT DO NOTHING
                """),
                    {**params, "version_id": version_id},
                )
            return UUID(str(app["application_id"]))

    @staticmethod
    def admit_head(
        *,
        connection: Connection,
        deployment_id: UUID,
        members: list[UUID],
        normalizer_version: str,
        adjudicator_versions: tuple[str, str],
    ) -> Mapping[str, Any] | None:
        """Admit a finite eligible set, then return only the canonical stream's head."""
        params = {
            "deployment_id": deployment_id,
            "members": members,
            "normalizer_version": normalizer_version,
            "adjudicator_versions": list(adjudicator_versions),
        }
        rows = (
            connection.execute(
                text("""
          SELECT a.application_id FROM fact_applications a JOIN claims c USING(deployment_id,claim_id)
          WHERE a.deployment_id=:deployment_id AND a.subject_entity_id=ANY(:members)
            AND a.normalizer_version=:normalizer_version AND a.adjudicator_version=ANY(:adjudicator_versions)
            AND a.applied_at IS NULL AND a.admission_sequence IS NULL
            AND EXISTS (
              SELECT 1 FROM normalize_observation_staging s JOIN obs_flush_entity_units u
                ON u.deployment_id=s.deployment_id AND u.version_id=s.version_id
               AND u.normalizer_version=s.normalizer_version AND u.subject_entity_id=s.subject_entity_id
              WHERE s.deployment_id=a.deployment_id AND s.application_id=a.application_id
                AND NOT EXISTS (SELECT 1 FROM processing_state p WHERE p.deployment_id=u.deployment_id
                  AND p.target_kind='entity' AND p.target_id=u.unit_id AND p.stage='adjudicate_observations'
                  AND p.status='dead_letter'))
          ORDER BY c.asserted_at NULLS LAST,c.claim_id,a.output_kind,a.output_ordinal,a.application_id LIMIT 128
        """),
                params,
            )
            .scalars()
            .all()
        )
        for application_id in rows:
            connection.execute(
                text("""UPDATE fact_applications
              SET admission_sequence=nextval('fact_application_admission_sequence')
              WHERE application_id=:application_id AND admission_sequence IS NULL
            """),
                {"application_id": application_id},
            )
        # Retire receipt-backed new memberships only after their entity units exist.
        connection.execute(
            text("""
          DELETE FROM normalize_observation_staging s USING fact_applications a
          WHERE s.deployment_id=:deployment_id AND s.application_id=a.application_id
            AND a.subject_entity_id=ANY(:members) AND a.applied_at IS NOT NULL
            AND EXISTS (SELECT 1 FROM obs_flush_entity_units u WHERE u.deployment_id=s.deployment_id
              AND u.version_id=s.version_id AND u.normalizer_version=s.normalizer_version
              AND u.subject_entity_id=s.subject_entity_id)
        """),
            params,
        )
        row = (
            connection.execute(
                text("""
          SELECT a.* FROM fact_applications a WHERE a.deployment_id=:deployment_id
            AND a.subject_entity_id=ANY(:members) AND a.applied_at IS NULL
            AND a.admission_sequence IS NOT NULL ORDER BY a.admission_sequence LIMIT 1
        """),
                params,
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def publish_decision(
        self,
        *,
        deployment_id: UUID,
        prepared: PreparedApplication,
        decision: FactApplicationDecision,
    ) -> bool:
        """CAS the first complete answer; late replies cannot replace another attempt."""
        with (
            self._engine.begin() as connection,
            application_fence(connection=connection, deployment_id=deployment_id),
        ):
            result = connection.execute(
                text("""
              UPDATE fact_applications a SET decision=CAST(:decision AS jsonb)
              WHERE deployment_id=:deployment_id AND application_id=:application_id
                AND attempt_id=:attempt_id AND input_hash=:input_hash
                AND decision IS NULL AND applied_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM unnest(a.input_claim_ids) id
                  WHERE NOT EXISTS (SELECT 1 FROM claims c WHERE c.deployment_id=a.deployment_id AND c.claim_id=id))
              RETURNING application_id
            """),
                {
                    "deployment_id": deployment_id,
                    "application_id": prepared.application_id,
                    "attempt_id": prepared.attempt_id,
                    "input_hash": prepared.input_hash,
                    "decision": decision.model_dump_json(),
                },
            )
            return result.scalar_one_or_none() is not None


def snapshot_hash(*, snapshot: Mapping[str, Any]) -> str:
    """Fingerprint complete domain inputs, excluding attempt and processing timestamps."""
    return sha256(canonical_json(dict(snapshot)).encode()).hexdigest()


_VERSION_APPLICATIONS = """
WITH version_claims AS (
 SELECT DISTINCT cl.claim_id,cl.doc_id FROM claims cl JOIN chunk_claims cc ON cc.claim_id=cl.claim_id
 JOIN chunks ch ON ch.chunk_id=cc.chunk_id
 WHERE cl.deployment_id=:deployment_id AND ch.deployment_id=:deployment_id
   AND ch.version_id=:version_id AND ch.representation_id=:representation_id
   AND ch.chunker_version=:chunker_version AND cl.extractor_version=:extractor_version
), expected AS (
 SELECT cl.*,n.normalizer_version AS receipt,x->>'kind' AS kind,(x->>'ordinal')::int AS ordinal
 FROM version_claims cl LEFT JOIN normalization_outputs n ON n.deployment_id=:deployment_id
   AND n.claim_id=cl.claim_id AND n.normalizer_version=:normalizer_version
 LEFT JOIN LATERAL jsonb_array_elements(n.accepted_outputs) x ON true
), applications AS (
 SELECT e.*,a.application_id,a.applied_at,a.subject_entity_id FROM expected e
 LEFT JOIN fact_applications a ON a.deployment_id=:deployment_id AND a.claim_id=e.claim_id
   AND a.normalizer_version=:normalizer_version AND a.output_kind=e.kind AND a.output_ordinal=e.ordinal
   AND a.adjudicator_version=CASE WHEN e.kind='relation' THEN :relation_version ELSE :observation_version END
)
"""


def register_version_applications_on(
    *, connection: Connection, parameters: dict[str, Any]
) -> None:
    """Attach later D56 version memberships from retained receipts before fan-out."""
    missing = connection.execute(
        text(
            _VERSION_APPLICATIONS
            + """
      SELECT EXISTS(SELECT 1 FROM applications WHERE receipt IS NULL OR (kind IS NOT NULL AND application_id IS NULL))
    """
        ),
        parameters,
    ).scalar_one()
    if missing:
        raise ApplicationInputChanged(
            "normalization barrier lacks a frozen response or accepted application"
        )
    connection.execute(
        text(
            _VERSION_APPLICATIONS
            + """
      INSERT INTO normalize_observation_staging(deployment_id,version_id,application_id,claim_id,
        subject_entity_id,statement,doc_id,normalizer_version)
      SELECT :deployment_id,:version_id,a.application_id,a.claim_id,a.subject_entity_id,
             CASE WHEN a.kind='observation' THEN n.output->'observations'->a.ordinal->>'statement' END,
             a.doc_id,:normalizer_version FROM applications a
      JOIN normalization_outputs n ON n.deployment_id=:deployment_id AND n.claim_id=a.claim_id
        AND n.normalizer_version=:normalizer_version
      WHERE a.application_id IS NOT NULL ON CONFLICT DO NOTHING
    """
        ),
        parameters,
    )


def version_applications_ready_on(
    *, connection: Connection, parameters: dict[str, Any]
) -> bool:
    """Prove exact frozen output membership applied, including zero-output receipts."""
    return not connection.execute(
        text(
            _VERSION_APPLICATIONS
            + """
      SELECT EXISTS(SELECT 1 FROM applications WHERE receipt IS NULL
        OR (kind IS NOT NULL AND (application_id IS NULL OR applied_at IS NULL)))
    """
        ),
        parameters,
    ).scalar_one()
