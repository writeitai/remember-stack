"""Maintenance upgrade orchestration across the committed D110 conversion boundary.

Operators stop intake, drain legacy work and stop old serving/worker processes
before invoking this entry point. It checks the drain and serializes upgrade
callers; an advisory lock cannot make an old binary honor a new runtime gate.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from uuid import UUID

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine.deployment_bootstrap import DeploymentBootstrapper
from rememberstack.spine.temporal_conversion import CONVERSION_SCHEMA_REVISION
from rememberstack.spine.temporal_conversion import TemporalFactConverter
from rememberstack.spine.temporal_journal import TEMPORAL_FACT_GENERATION
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from rememberstack.spine.temporal_schema import require_temporal_constraints_on
from rememberstack.spine.temporal_schema import TEMPORAL_FINAL_REVISION

_logger = logging.getLogger(__name__)


def upgrade_temporal_store(
    *,
    engine: Engine,
    config: Config,
    deployment_input: DeploymentBootstrapInput | None = None,
    batch_size: int = 64,
) -> None:
    """Commit C, resume every deployment's verified conversion, then commit D/head.

    A retry at C never reruns its exclusion removal. A retry at head never
    reconverts live facts or replaces a generation certificate. Empty identities
    created after D receive their explicit certificate in bootstrap's transaction.
    """
    converter = TemporalFactConverter(engine=engine, batch_size=batch_size)
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url is None or make_url(configured_url) != engine.url:
        raise TemporalWriteConflict(
            "upgrade configuration and converter must address the same database"
        )
    script = ScriptDirectory.from_config(config)
    lineage = tuple(revision.revision for revision in script.walk_revisions())
    head = script.get_current_head()
    if (
        head is None
        or CONVERSION_SCHEMA_REVISION not in lineage
        or TEMPORAL_FINAL_REVISION not in lineage
    ):
        raise TemporalWriteConflict(
            "upgrade graph is missing the temporal conversion boundary"
        )
    with _upgrade_lock(engine=engine) as connection:
        current = _current_revision(connection=connection)
        if current is not None and current not in lineage:
            raise TemporalWriteConflict(
                "database revision is outside this upgrade graph"
            )
        if current is None or lineage.index(current) > lineage.index(
            CONVERSION_SCHEMA_REVISION
        ):
            _require_legacy_drain(connection=connection)
            connection.commit()
            command.upgrade(config=config, revision=CONVERSION_SCHEMA_REVISION)
            current = _current_revision(connection=connection)
        if current == CONVERSION_SCHEMA_REVISION:
            _require_legacy_drain(connection=connection)
            if deployment_input is not None:
                DeploymentBootstrapper(engine=engine).bootstrap_deployment(
                    deployment_input=deployment_input
                )
            previous_id: UUID | None = None
            while True:
                deployment_id = connection.execute(
                    text("""
                    SELECT deployment_id FROM deployments
                    WHERE CAST(:previous AS uuid) IS NULL OR deployment_id > CAST(:previous AS uuid)
                    ORDER BY deployment_id LIMIT 1
                """),
                    {"previous": previous_id},
                ).scalar_one_or_none()
                if deployment_id is None:
                    break
                progress = converter.begin(deployment_id=deployment_id)
                while progress.phase != "complete":
                    if progress.phase == "preparing":
                        progress = converter.prepare_batch(deployment_id=deployment_id)
                    elif progress.phase == "converting":
                        progress = converter.apply_batch(deployment_id=deployment_id)
                    elif progress.phase == "verifying":
                        progress = converter.verify_batch(deployment_id=deployment_id)
                    else:
                        raise TemporalWriteConflict(
                            "recorded temporal conversion cannot advance from its current phase"
                        )
                    _logger.info(
                        "temporal conversion deployment=%s phase=%s verified=%s expected=%s",
                        deployment_id,
                        progress.phase,
                        progress.verified,
                        progress.expected,
                    )
                previous_id = deployment_id
            # This is deliberately a second Alembic invocation. C has committed
            # and every batch has durable progress before D sees the database.
            connection.commit()
            command.upgrade(config=config, revision="head")
        else:
            connection.commit()
            command.upgrade(config=config, revision="head")
            if deployment_input is not None:
                DeploymentBootstrapper(engine=engine).bootstrap_deployment(
                    deployment_input=deployment_input
                )
        if _current_revision(connection=connection) != head:
            raise TemporalWriteConflict(
                "temporal upgrade did not commit the expected schema head"
            )
        require_temporal_constraints_on(connection=connection)
        if connection.execute(
            _UNCERTIFIED, {"generation": TEMPORAL_FACT_GENERATION}
        ).scalar_one():
            raise TemporalWriteConflict(
                "deployment has no completed temporal fact generation"
            )


@contextmanager
def _upgrade_lock(*, engine: Engine) -> Iterator[Connection]:
    """Hold one database-local session lock across distinct Alembic transactions."""
    with engine.connect() as connection:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
            {"key": _UPGRADE_KEY},
        ).scalar_one()
        connection.commit()
        if not acquired:
            raise TemporalWriteConflict("another temporal schema upgrade is active")
        try:
            yield connection
        finally:
            connection.rollback()
            try:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": _UPGRADE_KEY},
                )
                connection.commit()
            except Exception:
                # A pooled connection must never retain a session lock after
                # an interrupted upgrade or a failed unlock round trip.
                connection.invalidate()
                raise


def _current_revision(*, connection: Connection) -> str | None:
    """Read the committed Alembic marker, rejecting multiple revision heads."""
    heads = MigrationContext.configure(connection=connection).get_current_heads()
    if len(heads) > 1:
        raise TemporalWriteConflict(
            "temporal upgrade requires one linear database revision"
        )
    return heads[0] if heads else None


def _require_legacy_drain(*, connection: Connection) -> None:
    """Refuse unfinished work before changing the legacy schema or resuming C."""
    for table, predicate in (
        ("processing_state", "status IN ('pending', 'running', 'failed')"),
        ("forget_manifests", "status <> 'complete'"),
    ):
        if (
            connection.execute(
                text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
            ).scalar_one()
            is None
        ):
            continue
        if connection.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM public.{table} WHERE {predicate})")
        ).scalar_one():
            raise TemporalWriteConflict(
                "stop intake and drain legacy work and accepted forgets before temporal upgrade"
            )


_UPGRADE_KEY = "rememberstack:temporal-schema-upgrade"
_UNCERTIFIED = text("""
    SELECT EXISTS (
        SELECT 1 FROM deployments deployment WHERE NOT EXISTS (
            SELECT 1 FROM temporal_fact_generations generation
            JOIN temporal_conversion_runs campaign USING (deployment_id, conversion_id)
            WHERE generation.deployment_id = deployment.deployment_id
              AND generation.generation = :generation AND campaign.generation = :generation
              AND campaign.state = 'complete'
        )
    )
""")
