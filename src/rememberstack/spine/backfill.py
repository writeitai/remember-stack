"""Bounded version-bump enumeration over the authoritative work ledger."""

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import BackfillSeedRequest
from rememberstack.model import BackfillSeedResult
from rememberstack.model import EnqueueWork
from rememberstack.model import LaneRouteError
from rememberstack.model import ProcessingLane
from rememberstack.spine.catalog_contract import lane_is_valid
from rememberstack.spine.work_ledger import enqueue_on


class BackfillSeederSettings(BaseSettings):
    """Bounded database work performed by each seeder transaction."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_BACKFILL_", extra="ignore"
    )

    batch_size: int = Field(default=500, ge=1, le=10_000)


class BackfillSeeder:
    """Seed a stage's new component version from its prior ledger targets.

    ``processing_state`` already records the generic target, input hash, and
    immutable handler payload for every stage execution. Reusing that ledger
    avoids a second stage-to-table registry and keeps a version bump independent
    of provider-specific catalogs. Rows for the requested version are excluded,
    so calling ``seed_batch`` again is both the cursor and the recovery path.
    """

    def __init__(self, *, engine: Engine, settings: BackfillSeederSettings) -> None:
        """Bind the seeder to the shared ledger database and explicit batch size."""
        self._engine = engine
        self._settings = settings

    def seed_batch(self, *, request: BackfillSeedRequest) -> BackfillSeedResult:
        """Insert at most one configured batch for a version-bump campaign.

        The latest prior row for each target supplies the immutable input
        payload. Every insert uses the ordinary ledger path and its D12 key;
        concurrent seeders therefore converge without duplicate work.
        """
        if not lane_is_valid(stage=request.stage, lane=ProcessingLane.BACKFILL.value):
            raise LaneRouteError(
                f"stage {request.stage} is not a plane-E stage and cannot be backfilled"
            )

        with self._engine.begin() as connection:
            candidates = (
                connection.execute(
                    _SELECT_CANDIDATES,
                    {
                        "deployment_id": request.deployment_id,
                        "stage": request.stage.value,
                        "component_version": request.component_version,
                        "batch_size": self._settings.batch_size,
                    },
                )
                .mappings()
                .all()
            )
            outcomes = tuple(
                enqueue_on(
                    connection=connection,
                    work=EnqueueWork(
                        deployment_id=request.deployment_id,
                        target_kind=row["target_kind"],
                        target_id=row["target_id"],
                        stage=request.stage,
                        component_version=request.component_version,
                        content_hash=row["content_hash"],
                        lane=ProcessingLane.BACKFILL,
                        payload=row["payload"],
                    ),
                )
                for row in candidates
            )

        created = sum(outcome.created for outcome in outcomes)
        return BackfillSeedResult(
            selected=len(candidates),
            created=created,
            already_present=len(outcomes) - created,
            complete=len(candidates) < self._settings.batch_size,
        )


_SELECT_CANDIDATES = text(
    """
    SELECT target_kind::text AS target_kind, target_id, content_hash, payload
    FROM (
        SELECT DISTINCT ON (source.target_kind, source.target_id)
               source.target_kind,
               source.target_id,
               source.content_hash,
               source.payload,
               source.enqueued_at,
               source.processing_id
        FROM processing_state AS source
        WHERE source.deployment_id = :deployment_id
          AND source.stage = :stage
          AND source.component_version <> :component_version
          AND NOT EXISTS (
              SELECT 1
              FROM processing_state AS seeded
              WHERE seeded.deployment_id = source.deployment_id
                AND seeded.target_kind = source.target_kind
                AND seeded.target_id = source.target_id
                AND seeded.stage = source.stage
                AND seeded.component_version = :component_version
          )
        ORDER BY source.target_kind, source.target_id,
                 source.enqueued_at DESC, source.processing_id DESC
    ) AS candidates
    ORDER BY target_kind, target_id
    LIMIT :batch_size
    """
)
