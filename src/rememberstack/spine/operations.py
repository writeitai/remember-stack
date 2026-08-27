"""Bounded read-only operational inspection over the durable Postgres truth."""

import re
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.sql.elements import TextClause

from rememberstack.model import CurrencyLedgerAudit
from rememberstack.model import CurrencyMismatch
from rememberstack.model import DeadLetterGroup
from rememberstack.model import DeadLetterRecord
from rememberstack.model import DeadLetterReport
from rememberstack.model import OperationalReport
from rememberstack.model import PipelineRouteStatus
from rememberstack.model import PoisonTargetRecord
from rememberstack.model import PoisonTargetReport
from rememberstack.model import ProjectionSnapshotState
from rememberstack.spine.lifecycle import CURRENCY_CACHE_MISMATCH_SQL

_ERROR_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class OperationalSettings(BaseSettings):
    """One cap shared by every variable-size operational detail collection."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_OPERATIONAL_", extra="ignore"
    )

    sample_limit: int = Field(default=20, ge=1, le=1_000)


class OperationalCatalog:
    """Build a coherent deployment report without a dashboard or control plane."""

    def __init__(self, *, engine: Engine, settings: OperationalSettings) -> None:
        """Bind an engine and the single explicit sample bound."""
        self._engine = engine
        self._settings = settings

    def inspect(self, *, deployment_id: UUID) -> OperationalReport:
        """Return aggregate truth plus bounded evidence in one repeatable-read view."""
        parameters = {
            "deployment_id": deployment_id,
            "sample_limit": self._settings.sample_limit,
        }
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            generated_at = connection.execute(text("SELECT now()")).scalar_one()
            routes = tuple(
                PipelineRouteStatus(**row)
                for row in connection.execute(_ROUTE_STATUS, parameters).mappings()
            )
            dead_letters = self._dead_letters(
                connection=connection, parameters=parameters
            )
            poison_targets = self._poison_targets(
                connection=connection, parameters=parameters
            )
            latest_projections = tuple(
                ProjectionSnapshotState(**row)
                for row in connection.execute(
                    _LATEST_PROJECTIONS, parameters
                ).mappings()
            )
            currency = self._currency(connection=connection, parameters=parameters)
        return OperationalReport(
            deployment_id=deployment_id,
            generated_at=generated_at,
            routes=routes,
            dead_letters=dead_letters,
            poison_targets=poison_targets,
            latest_projections=latest_projections,
            currency=currency,
        )

    def _dead_letters(
        self, *, connection: Connection, parameters: dict[str, object]
    ) -> DeadLetterReport:
        """Return DLQ totals and bounded grouped/detail evidence."""
        total = _count(connection=connection, statement=_DLQ_TOTAL, values=parameters)
        group_total = _count(
            connection=connection, statement=_DLQ_GROUP_TOTAL, values=parameters
        )
        groups = tuple(
            DeadLetterGroup(**row)
            for row in connection.execute(_DLQ_GROUPS, parameters).mappings()
        )
        items = tuple(
            DeadLetterRecord(
                **row, error_class=error_class_from_traceback(row["last_error"])
            )
            for row in connection.execute(_DLQ_ITEMS, parameters).mappings()
        )
        return DeadLetterReport(
            total=total, group_total=group_total, groups=groups, items=items
        )

    def _poison_targets(
        self, *, connection: Connection, parameters: dict[str, object]
    ) -> PoisonTargetReport:
        """Find targets whose same stage failed under multiple component versions."""
        total = _count(
            connection=connection, statement=_POISON_TOTAL, values=parameters
        )
        items = tuple(
            PoisonTargetRecord(
                target_kind=row["target_kind"],
                target_id=row["target_id"],
                stage=row["stage"],
                component_version_total=row["component_version_total"],
                component_versions=tuple(row["component_versions"]),
                dead_letters=row["dead_letters"],
            )
            for row in connection.execute(_POISON_ITEMS, parameters).mappings()
        )
        return PoisonTargetReport(total=total, items=items)

    def _currency(
        self, *, connection: Connection, parameters: dict[str, object]
    ) -> CurrencyLedgerAudit:
        """Apply the exact lifecycle invariant and bound its diagnostic rows."""
        claims = _count(
            connection=connection, statement=_CLAIM_TOTAL, values=parameters
        )
        mismatch_total = _count(
            connection=connection, statement=_CURRENCY_TOTAL, values=parameters
        )
        mismatches = tuple(
            CurrencyMismatch(**row)
            for row in connection.execute(_CURRENCY_ITEMS, parameters).mappings()
        )
        return CurrencyLedgerAudit(
            claims=claims, mismatch_total=mismatch_total, mismatches=mismatches
        )


def error_class_from_traceback(error: str | None) -> str:
    """Derive one stable class from the last non-empty traceback line."""
    if error is None:
        return "unknown"
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    if not lines:
        return "unknown"
    candidate = lines[-1].split(":", maxsplit=1)[0].strip()
    return candidate if _ERROR_CLASS.fullmatch(candidate) is not None else "unknown"


def _count(
    *, connection: Connection, statement: TextClause, values: dict[str, object]
) -> int:
    """Normalize one PostgreSQL count scalar."""
    return int(connection.execute(statement, values).scalar_one())


_ROUTE_STATUS = text(
    """
    SELECT stage, lane, status, count(*) AS count
    FROM processing_state
    WHERE deployment_id = :deployment_id
    GROUP BY stage, lane, status
    ORDER BY stage, lane NULLS FIRST, status
    """
)

_CLASSIFIED_DEAD_LETTERS = """
    WITH dead_letters AS (
        SELECT stage, component_version, enqueued_at, finished_at,
               btrim(reverse(split_part(
                   reverse(rtrim(coalesce(last_error, ''), E' \\t\\n\\r')),
                   E'\\n', 1
               ))) AS last_line
        FROM processing_state
        WHERE deployment_id = :deployment_id AND status = 'dead_letter'
    ), classified AS (
        SELECT stage, component_version, enqueued_at, finished_at,
               CASE
                   WHEN split_part(last_line, ':', 1)
                        ~ '^[A-Za-z_][A-Za-z0-9_.]*$'
                       THEN split_part(last_line, ':', 1)
                   ELSE 'unknown'
               END AS error_class
        FROM dead_letters
    )
"""

_DLQ_TOTAL = text(
    """
    SELECT count(*) FROM processing_state
    WHERE deployment_id = :deployment_id AND status = 'dead_letter'
    """
)

_DLQ_GROUP_TOTAL = text(
    _CLASSIFIED_DEAD_LETTERS
    + """
    SELECT count(*) FROM (
        SELECT 1 FROM classified
        GROUP BY stage, error_class, component_version
    ) groups
    """
)

_DLQ_GROUPS = text(
    _CLASSIFIED_DEAD_LETTERS
    + """
    SELECT stage, error_class, component_version, count(*) AS count,
           min(enqueued_at) AS oldest_enqueued_at,
           max(finished_at) AS latest_finished_at
    FROM classified
    GROUP BY stage, error_class, component_version
    ORDER BY count(*) DESC, stage, error_class, component_version
    LIMIT :sample_limit
    """
)

_DLQ_ITEMS = text(
    """
    SELECT processing_id, target_kind, target_id, stage, component_version,
           content_hash, lane, attempts, max_attempts, last_error, payload,
           enqueued_at, finished_at
    FROM processing_state
    WHERE deployment_id = :deployment_id AND status = 'dead_letter'
    ORDER BY finished_at DESC NULLS LAST, processing_id
    LIMIT :sample_limit
    """
)

_POISON_GROUPS = """
    SELECT target_kind, target_id, stage,
           count(DISTINCT component_version) AS component_version_total,
           count(*) AS dead_letters
    FROM processing_state
    WHERE deployment_id = :deployment_id AND status = 'dead_letter'
    GROUP BY target_kind, target_id, stage
    HAVING count(DISTINCT component_version) >= 2
"""

_POISON_TOTAL = text("SELECT count(*) FROM (" + _POISON_GROUPS + ") poison")

_POISON_ITEMS = text(
    "WITH poison AS ("
    + _POISON_GROUPS
    + ")"
    + """
    SELECT poison.*,
           ARRAY(
               SELECT DISTINCT versioned.component_version
               FROM processing_state versioned
               WHERE versioned.deployment_id = :deployment_id
                 AND versioned.status = 'dead_letter'
                 AND versioned.target_kind = poison.target_kind
                 AND versioned.target_id = poison.target_id
                 AND versioned.stage = poison.stage
               ORDER BY versioned.component_version
               LIMIT :sample_limit
           ) AS component_versions
    FROM poison
    ORDER BY poison.dead_letters DESC, poison.target_kind,
             poison.target_id, poison.stage
    LIMIT :sample_limit
    """
)

_LATEST_PROJECTIONS = text(
    """
    SELECT snapshot_id, plane, version, gcs_uri AS store_uri, row_counts,
           built_at, published_at
    FROM projection_snapshots
    WHERE deployment_id = :deployment_id
      AND plane = 'P3_corpusfs'
      AND is_latest
    ORDER BY plane
    """
)

_CLAIM_TOTAL = text("SELECT count(*) FROM claims WHERE deployment_id = :deployment_id")

_CURRENCY_TOTAL = text(
    "WITH mismatches AS ("
    + CURRENCY_CACHE_MISMATCH_SQL
    + ") SELECT count(*) FROM mismatches"
)

_CURRENCY_ITEMS = text(
    CURRENCY_CACHE_MISMATCH_SQL
    + """
    ORDER BY cl.claim_id
    LIMIT :sample_limit
    """
)
