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
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _VERSION_WORK,
                    {"deployment_id": deployment_id, "version_ids": version_ids},
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
