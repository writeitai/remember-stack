"""Machine-verifiable readiness for the ordinary self-host pipeline.

The work ledger remains authoritative. This read model checks the exact
component generations composed by a profile for each requested document
version, then verifies that P2 and P3 builds began after those terminal
E-stage rows. Publication time alone is insufficient: an older build can
finish after newer document work. This read does not execute work or hide
failures.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core import chunker_version as packing_generation
from rememberstack.core import ChunkerParams
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import PipelineStage
from rememberstack.model import PipelineStageReadiness
from rememberstack.model import ProjectionReadiness
from rememberstack.model import VersionPipelineReadiness
from rememberstack.spine.projection import ProjectionCatalog

_PLANES = ("P2_graph", "P3_corpusfs")


class PipelineReadinessCatalog:
    """Read exact per-version stage and aggregate-projection completion."""

    def __init__(
        self,
        *,
        engine: Engine,
        expected_components: Mapping[PipelineStage, str],
        projections: ProjectionCatalog,
        model_bindings: Mapping[str, str] | None = None,
        build_revision: str = "",
    ) -> None:
        """Bind the spine and the component generations this process serves."""
        self._engine = engine
        self._expected = tuple(expected_components.items())
        self._projections = projections
        self._model_bindings = dict(model_bindings or {})
        self._build_revision = build_revision

    def inspect(
        self,
        *,
        deployment_id: UUID,
        version_ids: tuple[UUID, ...],
        require_projections: bool,
    ) -> PipelineReadinessReport:
        """Return readiness without mutating or waiting for the pipeline."""
        version_ids = tuple(dict.fromkeys(version_ids))
        if not version_ids:
            raise ValueError("pipeline readiness requires at least one version_id")
        extract_version = next(
            (
                version
                for stage, version in self._expected
                if stage is PipelineStage.EXTRACT_CLAIMS
            ),
            None,
        )
        normalize_version = next(
            (
                version
                for stage, version in self._expected
                if stage is PipelineStage.NORMALIZE_RELATIONS
            ),
            None,
        )
        obs_flush_version = next(
            (
                version
                for stage, version in self._expected
                if stage is PipelineStage.ADJUDICATE_OBSERVATIONS
            ),
            None,
        )
        # Packing generation on chunk rows includes params (D58); the CHUNK
        # processing_state component_version is the bare algorithm pin. Use the
        # default pack params for the active grid filter (compose/selfhost default).
        chunk_version = packing_generation(params=ChunkerParams())
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _VERSION_WORK,
                    {"deployment_id": deployment_id, "version_ids": version_ids},
                )
                .mappings()
                .all()
            )
            extract_rows = ()
            if extract_version is not None and chunk_version is not None:
                extract_rows = (
                    connection.execute(
                        _EXTRACT_CHUNK_STATUS,
                        {
                            "deployment_id": deployment_id,
                            "version_ids": version_ids,
                            "extractor_version": extract_version,
                            "chunker_version": chunk_version,
                        },
                    )
                    .mappings()
                    .all()
                )
            normalize_rows = ()
            if (
                normalize_version is not None
                and chunk_version is not None
                and extract_version is not None
            ):
                normalize_rows = (
                    connection.execute(
                        _NORMALIZE_CLAIM_STATUS,
                        {
                            "deployment_id": deployment_id,
                            "version_ids": version_ids,
                            "normalize_version": normalize_version,
                            "chunker_version": chunk_version,
                            "extractor_version": extract_version,
                        },
                    )
                    .mappings()
                    .all()
                )
            obs_flush_rows = ()
            if obs_flush_version is not None:
                obs_flush_rows = (
                    connection.execute(
                        _ENTITY_OBS_FLUSH_STATUS,
                        {
                            "deployment_id": deployment_id,
                            "version_ids": version_ids,
                            "obs_flush_version": obs_flush_version,
                        },
                    )
                    .mappings()
                    .all()
                )
        by_key = {
            (
                UUID(str(row["target_id"])),
                PipelineStage(str(row["stage"])),
                str(row["component_version"]),
            ): row
            for row in rows
        }
        # Chunk-grain extract (D84): the derived status REPLACES a version-level
        # row, exactly as D88 normalize does below. The version-level
        # extract_claims row is the fan-out coordinator — under D84 it succeeds
        # once it has enqueued one job per chunk, which says nothing about
        # whether those jobs ran. Preferring it whenever it read `succeeded`
        # (the previous behaviour) let coordinator success mask a chunk-derived
        # `missing`, which is the same false-ready this aggregate exists to
        # prevent. Coordinator alone is not extract-complete.
        for row in extract_rows:
            key = (
                UUID(str(row["target_id"])),
                PipelineStage.EXTRACT_CLAIMS,
                str(row["component_version"]),
            )
            by_key[key] = row
        # Claim-grain normalize (D88): derived status replaces version-level
        # coordinator success (coordinator alone is not normalize-complete).
        for row in normalize_rows:
            key = (
                UUID(str(row["target_id"])),
                PipelineStage.NORMALIZE_RELATIONS,
                str(row["component_version"]),
            )
            by_key[key] = row
        # D90: entity-unit obs flush status replaces version-level coordinator.
        for row in obs_flush_rows:
            key = (
                UUID(str(row["target_id"])),
                PipelineStage.ADJUDICATE_OBSERVATIONS,
                str(row["component_version"]),
            )
            by_key[key] = row
        versions: list[VersionPipelineReadiness] = []
        terminal_at = None
        for version_id in version_ids:
            stages: list[PipelineStageReadiness] = []
            for stage, component_version in self._expected:
                row = by_key.get((version_id, stage, component_version))
                status = "missing" if row is None else str(row["status"])
                finished_at = None if row is None else row["finished_at"]
                stages.append(
                    PipelineStageReadiness.model_validate(
                        {
                            "stage": stage.value,
                            "component_version": component_version,
                            "status": status,
                            "finished_at": finished_at,
                        }
                    )
                )
                if finished_at is not None and (
                    terminal_at is None or finished_at > terminal_at
                ):
                    terminal_at = finished_at
            versions.append(
                VersionPipelineReadiness(
                    version_id=version_id,
                    ready=all(
                        item.status in {"succeeded", "skipped"}
                        and item.finished_at is not None
                        for item in stages
                    ),
                    stages=tuple(stages),
                )
            )
        projection_states: list[ProjectionReadiness] = []
        for plane in _PLANES:
            latest = self._projections.latest_snapshot(
                deployment_id=deployment_id, plane=plane
            )
            raw_built_at = None if latest is None else latest["built_at"]
            built_at = raw_built_at if isinstance(raw_built_at, datetime) else None
            raw_published_at = None if latest is None else latest["published_at"]
            published_at = (
                raw_published_at if isinstance(raw_published_at, datetime) else None
            )
            fresh = (
                latest is not None
                and built_at is not None
                and published_at is not None
                and terminal_at is not None
                and built_at >= terminal_at
            )
            projection_states.append(
                ProjectionReadiness(
                    plane=plane,
                    ready=fresh,
                    version=None if latest is None else str(latest["version"]),
                    built_at=built_at,
                    published_at=published_at,
                )
            )
        versions_ready = all(version.ready for version in versions)
        projections_ready = all(item.ready for item in projection_states)
        return PipelineReadinessReport(
            ready=versions_ready and (projections_ready or not require_projections),
            versions=tuple(versions),
            projections=tuple(projection_states),
            model_bindings=self._model_bindings,
            build_revision=self._build_revision,
        )


_VERSION_WORK = text(
    """
    SELECT target_id, stage::text AS stage, component_version,
           status::text AS status, finished_at
    FROM processing_state
    WHERE deployment_id = :deployment_id
      AND target_kind = 'document_version'
      AND target_id IN :version_ids
    """
).bindparams(bindparam("version_ids", expanding=True))

# D84: extract_claims primary rows target chunks; derive a version-level status
# for the version's current representation only.
_EXTRACT_CHUNK_STATUS = text(
    """
    -- `finished_at` mirrors the ledger's own invariant: it is stamped exactly
    -- on the terminal transitions (`succeeded`, `dead_letter`) and cleared
    -- again when a row leaves terminal (`_REPLAY_DEAD_LETTER` sets it NULL).
    -- Two things broke that here. A `now()` fallback in the COALESCE fabricated
    -- a completion instant for work that never completed — a different one on
    -- every inspection — so it was removed; a stage with no observed completion
    -- now reports none. And because the status is DERIVED by aggregating over
    -- chunks, a still-running stage can hold real timestamps from the chunks
    -- that already succeeded, so the outer CASE suppresses those. It admits
    -- `dead_letter`: that is terminal and its ledger timestamp is the honest
    -- answer to "when did this stop", which an operator needs most precisely
    -- when the stage has died.
    SELECT target_id, stage, component_version, status,
           CASE
             WHEN status IN ('succeeded', 'dead_letter') THEN finished_at
           END AS finished_at
    FROM (
        SELECT v.version_id AS target_id,
               'extract_claims'::text AS stage,
               :extractor_version AS component_version,
               CASE
                 -- No current representation at all: convert/structure have not
                 -- produced one, so extraction cannot have happened. This is NOT
                 -- the D84 empty-document case — an empty document still HAS a
                 -- representation, it just yields zero chunks. Collapsing the two
                 -- reported `succeeded` for a version that had not been converted.
                 WHEN count(r.representation_id) = 0 THEN 'missing'
                 -- A representation with no chunks AT ALL is genuinely terminal
                 -- (D84: embed hops straight to normalize for an empty document).
                 WHEN COALESCE(max(any_chunks.total), 0) = 0 THEN 'succeeded'
                 -- Chunks exist, but none under the grid this readiness check is
                 -- asking about. That is NOT terminal: it means extraction for the
                 -- generation we can see never ran, and reporting 'succeeded' here
                 -- told an agent its memory was queryable when nothing had been
                 -- extracted. Refuse to claim success we cannot observe.
                 WHEN count(c.chunk_id) = 0 THEN 'missing'
                 WHEN count(c.chunk_id) FILTER (
                        WHERE p.status = 'succeeded'
                      ) = count(c.chunk_id)
                   THEN 'succeeded'
                 WHEN bool_or(p.status = 'dead_letter') THEN 'dead_letter'
                 WHEN bool_or(p.status = 'running') THEN 'running'
                 WHEN bool_or(p.status = 'failed') THEN 'failed'
                 WHEN bool_or(p.status = 'pending') THEN 'pending'
                 ELSE 'missing'
               END AS status,
               -- No `now()` fallback: for the D84 empty-document arm the honest
               -- completion time is the version's embed_chunk success, which the
               -- worker stamps even when there are zero chunks. If that row is
               -- absent, embed never ran, so extraction cannot be complete and
               -- this correctly yields NULL rather than inventing a timestamp
               -- that would make the version report ready.
               COALESCE(
                 max(p.finished_at),
                 max(embed.finished_at)
               ) AS finished_at
        FROM document_versions v
        LEFT JOIN document_representations r
          ON r.representation_id = v.current_representation_id
        -- Grid-independent chunk presence, as a LATERAL scalar rather than a
        -- second join on `chunks`: joining twice would multiply this row set by the
        -- chunk count (a 1k-chunk representation becomes 1M rows) for a number we
        -- only need once per version.
        LEFT JOIN LATERAL (
          SELECT count(*) AS total
          FROM chunks ac
          WHERE ac.representation_id = r.representation_id
        ) any_chunks ON TRUE
        LEFT JOIN chunks c
          ON c.representation_id = r.representation_id
         AND c.chunker_version = :chunker_version
        LEFT JOIN processing_state p
          ON p.deployment_id = :deployment_id
         AND p.target_kind = 'chunk'
         AND p.target_id = c.chunk_id
         AND p.stage = 'extract_claims'
         AND p.component_version = :extractor_version
        LEFT JOIN processing_state embed
          ON embed.deployment_id = :deployment_id
         AND embed.target_kind = 'document_version'
         AND embed.target_id = v.version_id
         AND embed.stage = 'embed_chunk'
         AND embed.status = 'succeeded'
        WHERE v.version_id IN :version_ids
        GROUP BY v.version_id
    
    ) derived
    """
).bindparams(bindparam("version_ids", expanding=True))

# D88: normalize_relations primary rows target claims; derive version status.
# dead_letter blocks readiness. Version-level coordinator success is ignored.
_NORMALIZE_CLAIM_STATUS = text(
    """
    -- Same terminal gate as the extract block above, for the same reason: the
    -- status is derived by aggregating over claims, so a stage still running
    -- can hold real timestamps from claims that already succeeded. Normalize
    -- never had the `now()` fabrication, so this only suppresses a misleading
    -- completion time; it never invents one.
    SELECT target_id, stage, component_version, status,
           CASE
             WHEN status IN ('succeeded', 'dead_letter') THEN finished_at
           END AS finished_at
    FROM (
    SELECT v.version_id AS target_id,
           'normalize_relations'::text AS stage,
           :normalize_version AS component_version,
           CASE
             WHEN count(cl.claim_id) = 0 THEN 'succeeded'
             WHEN count(cl.claim_id) FILTER (
                    WHERE p.status = 'succeeded'
                  ) = count(cl.claim_id)
               THEN 'succeeded'
             WHEN bool_or(p.status = 'dead_letter') THEN 'dead_letter'
             WHEN bool_or(p.status = 'running') THEN 'running'
             WHEN bool_or(p.status = 'failed') THEN 'failed'
             WHEN bool_or(p.status = 'pending') THEN 'pending'
             ELSE 'missing'
           END AS status,
           COALESCE(
             max(p.finished_at),
             max(embed.finished_at)
           ) AS finished_at
    FROM document_versions v
    LEFT JOIN document_representations r
      ON r.representation_id = v.current_representation_id
    LEFT JOIN chunks c
      ON c.representation_id = r.representation_id
     AND c.chunker_version = :chunker_version
    -- D56: occurrence map is chunk_claims; claims.chunk_id is origin only.
    LEFT JOIN chunk_claims cc
      ON cc.chunk_id = c.chunk_id
    LEFT JOIN claims cl
      ON cl.claim_id = cc.claim_id
     AND cl.extractor_version = :extractor_version
    LEFT JOIN processing_state p
      ON p.deployment_id = :deployment_id
     AND p.target_kind = 'claim'
     AND p.target_id = cl.claim_id
     AND p.stage = 'normalize_relations'
     AND p.component_version = :normalize_version
    LEFT JOIN processing_state embed
      ON embed.deployment_id = :deployment_id
     AND embed.target_kind = 'document_version'
     AND embed.target_id = v.version_id
     AND embed.stage = 'embed_chunk'
     AND embed.status = 'succeeded'
    WHERE v.version_id IN :version_ids
    GROUP BY v.version_id
    ) derived
    """
).bindparams(bindparam("version_ids", expanding=True))

# D90: derive adjudicate_observations from entity units / version_state.
_ENTITY_OBS_FLUSH_STATUS = text(
    """
    SELECT target_id, stage, component_version, status,
           CASE WHEN status IN ('succeeded', 'dead_letter') THEN finished_at END AS finished_at
    FROM (
    SELECT v.version_id AS target_id,
           'adjudicate_observations'::text AS stage,
           :obs_flush_version AS component_version,
           CASE
             WHEN bool_or(s.fanout_status IN ('empty_complete', 'barrier_complete'))
               THEN 'succeeded'
             WHEN count(u.unit_id) = 0 THEN 'missing'
             WHEN count(u.unit_id) FILTER (WHERE p.status = 'succeeded') = count(u.unit_id)
               THEN 'succeeded'
             WHEN bool_or(p.status = 'dead_letter') THEN 'dead_letter'
             WHEN bool_or(p.status = 'running') THEN 'running'
             WHEN bool_or(p.status = 'failed') THEN 'failed'
             WHEN bool_or(p.status = 'pending') THEN 'pending'
             ELSE 'missing'
           END AS status,
           max(p.finished_at) AS finished_at
    FROM document_versions v
    LEFT JOIN obs_flush_version_state s
      ON s.deployment_id = :deployment_id
     AND s.version_id = v.version_id
    LEFT JOIN obs_flush_entity_units u
      ON u.deployment_id = :deployment_id
     AND u.version_id = v.version_id
    LEFT JOIN processing_state p
      ON p.deployment_id = u.deployment_id
     AND p.target_kind = 'entity'
     AND p.target_id = u.unit_id
     AND p.stage = 'adjudicate_observations'
     AND p.component_version = :obs_flush_version
    WHERE v.version_id IN :version_ids
    GROUP BY v.version_id
    ) derived
    """
).bindparams(bindparam("version_ids", expanding=True))
