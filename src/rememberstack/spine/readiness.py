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
        chunk_version = next(
            (
                version
                for stage, version in self._expected
                if stage is PipelineStage.CHUNK
            ),
            None,
        )
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
        by_key = {
            (
                UUID(str(row["target_id"])),
                PipelineStage(str(row["stage"])),
                str(row["component_version"]),
            ): row
            for row in rows
        }
        # Chunk-grain extract (D84) wins over a missing version-level extract row.
        for row in extract_rows:
            key = (
                UUID(str(row["target_id"])),
                PipelineStage.EXTRACT_CLAIMS,
                str(row["component_version"]),
            )
            existing = by_key.get(key)
            if existing is None or str(existing["status"]) == "missing":
                by_key[key] = row
            elif str(existing["status"]) != "succeeded" and str(row["status"]) == (
                "succeeded"
            ):
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
    SELECT v.version_id AS target_id,
           'extract_claims'::text AS stage,
           :extractor_version AS component_version,
           CASE
             WHEN count(c.chunk_id) = 0 THEN 'succeeded'
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
           COALESCE(
             max(p.finished_at),
             max(embed.finished_at),
             now()
           ) AS finished_at
    FROM document_versions v
    LEFT JOIN document_representations r
      ON r.representation_id = v.current_representation_id
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
    """
).bindparams(bindparam("version_ids", expanding=True))
