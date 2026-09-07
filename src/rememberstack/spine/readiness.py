"""Machine-verifiable readiness for the ordinary self-host pipeline.

The work ledger remains authoritative. This read model checks exact pipeline
generations, current P1 configuration, live PostgreSQL graph catalog/query
health, and the optional P3 publication boundary. It never executes work.
"""

from collections.abc import Mapping
from datetime import datetime
import time
from typing import Literal
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from rememberstack.core import chunker_version as packing_generation
from rememberstack.core import ChunkerParams
from rememberstack.model import CapabilityReadiness
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import PipelineStage
from rememberstack.model import PipelineStageReadiness
from rememberstack.model import ReadinessRequirements
from rememberstack.model import VersionPipelineReadiness
from rememberstack.spine.graph_catalog import graph_catalog_problems
from rememberstack.spine.postgres_graph_sql import CURRENT_NEIGHBORHOOD_GUARD
from rememberstack.spine.postgres_graph_sql import CURRENT_NEIGHBORHOOD_PGQ
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_GUARD
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_PGQ
from rememberstack.spine.projection import ProjectionCatalog


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
        self._graph_catalog_verified_at: float | None = None
        self._graph_catalog_cache_seconds = 30.0

    def inspect(
        self,
        *,
        deployment_id: UUID,
        version_ids: tuple[UUID, ...],
        require: ReadinessRequirements,
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
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            checked_at = connection.execute(
                text("SELECT statement_timestamp()")
            ).scalar_one()
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
            if obs_flush_version is not None and normalize_version is not None:
                obs_flush_rows = (
                    connection.execute(
                        _ENTITY_OBS_FLUSH_STATUS,
                        {
                            "deployment_id": deployment_id,
                            "version_ids": version_ids,
                            "obs_flush_version": obs_flush_version,
                            "normalizer_version": normalize_version,
                        },
                    )
                    .mappings()
                    .all()
                )
            p1_ready = bool(
                connection.execute(
                    _P1_READY, {"deployment_id": deployment_id}
                ).scalar_one()
            )
            document_binding_generation = connection.execute(
                _DOCUMENT_BINDING_GENERATION, {"deployment_id": deployment_id}
            ).scalar_one()
            verify_graph_catalog = (
                self._graph_catalog_verified_at is None
                or time.monotonic() - self._graph_catalog_verified_at
                >= self._graph_catalog_cache_seconds
            )
            live_graph_ready, live_graph_reason = _live_graph_status(
                connection=connection,
                deployment_id=deployment_id,
                checked_at=checked_at,
                verify_catalog=verify_graph_catalog,
            )
            if live_graph_ready and verify_graph_catalog:
                self._graph_catalog_verified_at = time.monotonic()
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
        versions_ready = all(version.ready for version in versions)
        latest_p3 = self._projections.latest_snapshot(
            deployment_id=deployment_id, plane="P3_corpusfs"
        )
        raw_p3_built_at = None if latest_p3 is None else latest_p3["built_at"]
        p3_built_at = raw_p3_built_at if isinstance(raw_p3_built_at, datetime) else None
        raw_p3_published_at = None if latest_p3 is None else latest_p3["published_at"]
        p3_published_at = (
            raw_p3_published_at if isinstance(raw_p3_published_at, datetime) else None
        )
        p3_ready = (
            latest_p3 is not None
            and p3_published_at is not None
            and terminal_at is not None
            and p3_built_at is not None
            and p3_built_at >= terminal_at
        )
        capabilities: dict[
            Literal["pipeline", "p1", "live_graph", "p3"], CapabilityReadiness
        ] = {
            "pipeline": CapabilityReadiness(
                required=require.pipeline,
                ready=versions_ready,
                checked_at=checked_at,
                reason="ready" if versions_ready else "stage_incomplete",
            ),
            "p1": CapabilityReadiness(
                required=require.p1,
                ready=p1_ready,
                checked_at=checked_at,
                reason="ready" if p1_ready else "search_channel_incomplete",
            ),
            "live_graph": CapabilityReadiness(
                required=require.live_graph,
                ready=live_graph_ready,
                checked_at=checked_at,
                reason=live_graph_reason,
            ),
            "p3": CapabilityReadiness(
                required=require.p3,
                ready=p3_ready,
                checked_at=checked_at,
                reason="ready" if p3_ready else "corpus_snapshot_incomplete",
                version=None if latest_p3 is None else str(latest_p3["version"]),
                built_at=p3_built_at,
                published_at=p3_published_at,
            ),
        }
        ready = all(
            not capability.required or capability.ready
            for capability in capabilities.values()
        )
        return PipelineReadinessReport(
            ready=ready,
            versions=tuple(versions),
            capabilities=capabilities,
            document_binding_generation=document_binding_generation,
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

_P1_READY = text(
    """
    SELECT count(*) = 7 AND coalesce(bool_and(ready), false)
    FROM p1_search_channels
    WHERE deployment_id = :deployment_id
      AND (target, channel) IN (
        ('chunks', 'semantic'),
        ('claims', 'semantic'),
        ('relations', 'semantic'),
        ('observations', 'semantic'),
        ('entities', 'semantic'),
        ('chunks', 'bm25'),
        ('claims', 'bm25')
      )
    """
)

_DOCUMENT_BINDING_GENERATION = text(
    """
    SELECT document_binding_generation
    FROM deployments
    WHERE deployment_id = :deployment_id
    """
)

_ABSENT_GRAPH_ANCHOR = text(
    """
    SELECT NOT EXISTS (
             SELECT 1
             FROM memory_v1.entities_current
             WHERE deployment_id = :deployment_id AND entity_id = :candidate_id
           )
       AND NOT EXISTS (
             SELECT 1
             FROM memory_v1.documents_live
             WHERE deployment_id = :deployment_id AND doc_id = :candidate_id
           )
    """
)

_NEIGHBORHOOD_HEALTH = text(
    """
    SELECT *
    FROM memory_v1.graph_neighborhood(
      :deployment_id, :candidate_id, 1, NULL, :checked_at, :checked_at,
      1, 8, 8, 1000
    )
    """
)

_PATH_HEALTH = text(
    """
    SELECT *
    FROM memory_v1.graph_path(
      :deployment_id, :candidate_id, :candidate_id, 1, NULL,
      :checked_at, :checked_at, 1, 8, 8, 1000
    )
    """
)

_CITATION_PATH_HEALTH = text(
    """
    SELECT *
    FROM memory_v1.graph_citation_path(
      :deployment_id, :candidate_id, :candidate_id, 1, 1, 8, 8, 1000
    )
    """
)


def _catalog_problem_reason(problem: str) -> str:
    """Map detailed non-secret catalog diagnostics to a stable reason code."""
    if problem.startswith("server_version_num"):
        return "graph_server_version_mismatch"
    if problem.startswith("extension versions"):
        return "graph_extension_version_mismatch"
    if "role" in problem:
        return "graph_role_contract_mismatch"
    if problem.startswith("helper"):
        return "graph_helper_contract_mismatch"
    return "graph_catalog_mismatch"


def _live_graph_status(
    *,
    connection: Connection,
    deployment_id: UUID,
    checked_at: datetime,
    verify_catalog: bool,
) -> tuple[bool, str]:
    """Prove catalog and bounded query health with a stable failure reason."""
    role_active = False
    try:
        if verify_catalog:
            problems = graph_catalog_problems(connection=connection)
            if problems:
                return False, _catalog_problem_reason(problems[0])
        quoted_role = str(
            connection.execute(
                text("SELECT quote_ident('rememberstack_graph_' || current_database())")
            ).scalar_one()
        )
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '500ms'")
        connection.exec_driver_sql(
            "SET LOCAL idle_in_transaction_session_timeout = '5s'"
        )
        connection.exec_driver_sql("SET LOCAL transaction_timeout = '6s'")
        connection.exec_driver_sql("SET LOCAL temp_file_limit = '65536kB'")
        connection.exec_driver_sql("SET LOCAL max_parallel_workers_per_gather = 0")
        connection.exec_driver_sql("SET LOCAL search_path = memory_v1, pg_catalog")
        connection.exec_driver_sql("SET LOCAL work_mem = '16384kB'")
        connection.exec_driver_sql(f"SET LOCAL ROLE {quoted_role}")
        role_active = True
        limits_ready = bool(
            connection.execute(
                text(
                    "SELECT current_user = "
                    "'rememberstack_graph_' || current_database() "
                    "AND current_setting('statement_timeout')::interval = "
                    "interval '5 seconds' "
                    "AND current_setting('lock_timeout')::interval = "
                    "interval '500 milliseconds' "
                    "AND current_setting("
                    "'idle_in_transaction_session_timeout')::interval = "
                    "interval '5 seconds' "
                    "AND current_setting('transaction_timeout')::interval = "
                    "interval '6 seconds' "
                    "AND pg_size_bytes(current_setting('temp_file_limit')) = "
                    "67108864 "
                    "AND pg_size_bytes(current_setting('work_mem')) = 16777216 "
                    "AND current_setting("
                    "'max_parallel_workers_per_gather')::integer = 0 "
                    "AND current_setting('search_path') = "
                    "'memory_v1, pg_catalog'"
                )
            ).scalar_one()
        )
        if not limits_ready:
            return False, "graph_role_runtime_limits_mismatch"
        parameters: dict[str, object] | None = None
        for _attempt in range(2):
            candidate_id = uuid4()
            candidate_parameters: dict[str, object] = {
                "deployment_id": deployment_id,
                "candidate_id": candidate_id,
                "checked_at": checked_at,
            }
            if bool(
                connection.execute(
                    _ABSENT_GRAPH_ANCHOR, candidate_parameters
                ).scalar_one()
            ):
                parameters = candidate_parameters
                break
        if parameters is None:
            return False, "graph_smoke_identifier_collision"
        pgq_parameters = {
            **parameters,
            "anchor_id": parameters["candidate_id"],
            "max_depth": 1,
            "predicates": None,
            "max_results": 1,
            "expansion_budget": 8,
            "frontier_budget": 8,
            "time_budget_ms": 1000,
            "valid_at": checked_at,
            "believed_at": checked_at,
            "result_offset": 0,
        }
        for guard_statement, pgq_statement in (
            (CURRENT_NEIGHBORHOOD_GUARD, CURRENT_NEIGHBORHOOD_PGQ),
            (HISTORY_NEIGHBORHOOD_GUARD, HISTORY_NEIGHBORHOOD_PGQ),
        ):
            guard_rows = (
                connection.execute(text(guard_statement), pgq_parameters)
                .mappings()
                .all()
            )
            if len(guard_rows) != 1 or not bool(guard_rows[0]["admitted"]):
                return False, "graph_pgq_guard_smoke_failed"
            if connection.execute(text(pgq_statement), pgq_parameters).first():
                return False, "graph_pgq_smoke_failed"
        for reason, statement in (
            ("graph_neighborhood_smoke_failed", _NEIGHBORHOOD_HEALTH),
            ("graph_path_smoke_failed", _PATH_HEALTH),
            ("graph_citation_smoke_failed", _CITATION_PATH_HEALTH),
        ):
            rows = connection.execute(statement, parameters).mappings().all()
            if any(row["row_kind"] == "data" for row in rows):
                return False, reason
            if sum(row["row_kind"] == "status" for row in rows) != 1:
                return False, reason
        connection.exec_driver_sql("RESET ROLE")
        role_active = False
        return True, "ready"
    except SQLAlchemyError:
        return False, "graph_database_or_permission_failed"
    except (KeyError, TypeError, ValueError):
        return False, "graph_smoke_contract_failed"
    finally:
        if role_active:
            try:
                connection.exec_driver_sql("RESET ROLE")
            except SQLAlchemyError:
                pass


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
# State and membership are pinned to the active normalizer generation so an
# older empty_complete cannot satisfy a newer composed generation.
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
           COALESCE(max(s.completed_at), max(p.finished_at)) AS finished_at
    FROM document_versions v
    LEFT JOIN obs_flush_version_state s
      ON s.deployment_id = :deployment_id
     AND s.version_id = v.version_id
     AND s.normalizer_version = :normalizer_version
    LEFT JOIN obs_flush_entity_units u
      ON u.deployment_id = :deployment_id
     AND u.version_id = v.version_id
     AND u.normalizer_version = :normalizer_version
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
