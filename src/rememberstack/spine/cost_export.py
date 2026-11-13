"""Content-free cost export reader for ``rememberstack.cost_export.v1`` (D91).

HTTP and ``remember ops cost-export`` are thin callers of this module. The
field set is frozen: a later contract is a new path, not new columns here.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode
from base64 import urlsafe_b64encode
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
import json
from typing import Final
from typing import Literal
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_serializer
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

COST_EXPORT_CONTRACT: Final = "rememberstack.cost_export.v1"
COST_EXPORT_MIN_TOKEN_BYTES: Final = 32
COST_EXPORT_DEFAULT_LIMIT: Final = 100
COST_EXPORT_MAX_LIMIT: Final = 500
COST_EXPORT_SAFETY_LAG: Final = timedelta(seconds=60)

COST_EXPORT_PAGE_FIELDS: Final[tuple[str, ...]] = (
    "contract",
    "deployment_id",
    "server_time",
    "horizon",
    "cursor",
    "next_cursor",
    "persist_failures",
    "scope_missing",
    "receipts",
)
COST_EXPORT_RECEIPT_FIELDS: Final[tuple[str, ...]] = (
    "cost_id",
    "deployment_id",
    "source",
    "work_id",
    "stage",
    "lane",
    "attempt",
    "surface",
    "call_key",
    "outcome",
    "model_name",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "occurred_at",
)

_ZERO_OCCURRED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ZERO_COST_ID = UUID("00000000-0000-0000-0000-000000000000")


class CostExportConfigError(ValueError):
    """HTTP export was requested with a bind or token that cannot be served."""


class CostExportCursorError(ValueError):
    """The supplied cursor is not a well-formed v1 cursor."""


class CostExportSettings(BaseSettings):
    """Dedicated export listener settings; not the self-host or client prefix."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    cost_export_token: SecretStr | None = None
    cost_export_bind: str | None = None

    def http_bind(self) -> str | None:
        """Return the configured bind, or ``None`` when HTTP export is off."""
        bind = (self.cost_export_bind or "").strip()
        return bind or None

    def require_http_credentials(self) -> tuple[str, str]:
        """Return bind and token, or refuse an unauthenticated listener."""
        bind = self.http_bind()
        if bind is None:
            raise CostExportConfigError("REMEMBERSTACK_COST_EXPORT_BIND is not set")
        token = self.cost_export_token
        if token is None:
            raise CostExportConfigError(
                "REMEMBERSTACK_COST_EXPORT_TOKEN is required when the export bind is set"
            )
        value = token.get_secret_value()
        if len(value.encode("utf-8")) < COST_EXPORT_MIN_TOKEN_BYTES:
            raise CostExportConfigError(
                "REMEMBERSTACK_COST_EXPORT_TOKEN must be at least 32 bytes when the "
                "export bind is set"
            )
        return bind, value


class CostExportReceipt(BaseModel):
    """One allowlisted receipt. Extra keys are forbidden."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cost_id: UUID
    deployment_id: UUID
    source: Literal["worker", "surface"]
    work_id: UUID
    stage: str | None
    lane: str | None
    attempt: int | None
    surface: str | None
    call_key: str
    outcome: str
    model_name: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: Decimal | None
    latency_ms: int | None
    occurred_at: datetime

    @field_serializer("cost_usd")
    def serialize_cost_usd(self, value: Decimal | None) -> str | None:
        """Serialize native-scale decimals as strings; keep SQL NULL as JSON null."""
        if value is None:
            return None
        return format(value, "f")

    @field_serializer("occurred_at")
    def serialize_occurred_at(self, value: datetime) -> str:
        """Emit RFC 3339 UTC."""
        return rfc3339_utc(value=value)


class CostExportPage(BaseModel):
    """One v1 export page. Extra keys are forbidden."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["rememberstack.cost_export.v1"]
    deployment_id: UUID
    server_time: datetime
    horizon: datetime
    cursor: str
    next_cursor: str
    persist_failures: int = Field(ge=0)
    scope_missing: int = Field(ge=0)
    receipts: tuple[CostExportReceipt, ...]

    @field_serializer("server_time", "horizon")
    def serialize_page_time(self, value: datetime) -> str:
        """Emit RFC 3339 UTC for page clocks."""
        return rfc3339_utc(value=value)


class CostExportReader(Protocol):
    """Read one v1 page from the operator spend view."""

    def read_page(
        self, *, deployment_id: UUID, cursor: str | None, limit: int
    ) -> CostExportPage:
        """Return one allowlisted page for ``deployment_id``."""
        ...


class SqlCostExportReader:
    """REPEATABLE READ reader of ``v_cost_receipts`` plus meter-state counters."""

    def __init__(
        self, *, engine: Engine, safety_lag: timedelta = COST_EXPORT_SAFETY_LAG
    ) -> None:
        """Bind the spine engine. ``safety_lag`` is 60s in production."""
        self._engine = engine
        self._safety_lag = safety_lag

    def read_page(
        self, *, deployment_id: UUID, cursor: str | None, limit: int
    ) -> CostExportPage:
        """Snapshot the view, apply the frozen horizon, and emit a v1 page."""
        if limit < 1 or limit > COST_EXPORT_MAX_LIMIT:
            raise ValueError(
                f"limit must be between 1 and {COST_EXPORT_MAX_LIMIT}, not {limit}"
            )
        incoming = decode_cost_export_cursor(cursor=cursor)
        with self._engine.connect() as connection:
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
            with connection.begin():
                server_time = _as_utc(
                    value=connection.execute(
                        text("SELECT clock_timestamp()")
                    ).scalar_one()
                )
                request_horizon = server_time - self._safety_lag
                upper_bound = request_horizon
                if incoming is not None:
                    upper_bound = min(incoming.horizon_at_issue, request_horizon)
                rows = connection.execute(
                    _SELECT_RECEIPTS,
                    {
                        "deployment_id": deployment_id,
                        "upper_bound": upper_bound,
                        "use_key": incoming is not None,
                        "last_occurred_at": (
                            incoming.last_occurred_at
                            if incoming is not None
                            else _ZERO_OCCURRED_AT
                        ),
                        "last_source": incoming.last_source
                        if incoming is not None
                        else "",
                        "last_cost_id": (
                            incoming.last_cost_id
                            if incoming is not None
                            else _ZERO_COST_ID
                        ),
                        "limit": limit,
                    },
                ).mappings()
                receipts = tuple(_receipt_from_row(row=dict(row)) for row in rows)
                meter = (
                    connection.execute(
                        _SELECT_METER_STATE, {"deployment_id": deployment_id}
                    )
                    .mappings()
                    .first()
                )
        persist_failures = int(meter["persist_failures"]) if meter is not None else 0
        scope_missing = int(meter["scope_missing"]) if meter is not None else 0
        last_key = _last_key(receipts=receipts, incoming=incoming)
        # Echo the request cursor when present. A first page issues the zero key
        # with this request's horizon. ``next_cursor`` always refreshes horizon.
        if incoming is None:
            issued_cursor = encode_cost_export_cursor(
                last_occurred_at=_ZERO_OCCURRED_AT,
                last_source="",
                last_cost_id=_ZERO_COST_ID,
                horizon_at_issue=request_horizon,
            )
        else:
            issued_cursor = cursor or incoming.encode()
        next_cursor = encode_cost_export_cursor(
            last_occurred_at=last_key[0],
            last_source=last_key[1],
            last_cost_id=last_key[2],
            horizon_at_issue=request_horizon,
        )
        return CostExportPage(
            contract=COST_EXPORT_CONTRACT,
            deployment_id=deployment_id,
            server_time=server_time,
            horizon=request_horizon,
            cursor=issued_cursor if incoming is None else issued_cursor,
            next_cursor=next_cursor,
            persist_failures=persist_failures,
            scope_missing=scope_missing,
            receipts=receipts,
        )


class _CursorPayload(BaseModel):
    """Decoded cursor key plus the frozen horizon at issue."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    last_occurred_at: datetime
    last_source: str
    last_cost_id: UUID
    horizon_at_issue: datetime

    def encode(self) -> str:
        """Serialize this payload as an opaque cursor."""
        return encode_cost_export_cursor(
            last_occurred_at=self.last_occurred_at,
            last_source=self.last_source,
            last_cost_id=self.last_cost_id,
            horizon_at_issue=self.horizon_at_issue,
        )


def rfc3339_utc(*, value: datetime) -> str:
    """Format a timestamp as RFC 3339 UTC with a ``Z`` suffix."""
    aware = _as_utc(value=value)
    return aware.isoformat().replace("+00:00", "Z")


def parse_rfc3339(*, value: str) -> datetime:
    """Parse an RFC 3339 timestamp into UTC."""
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CostExportCursorError("cursor timestamp is not RFC 3339") from error
    return _as_utc(value=parsed)


def encode_cost_export_cursor(
    *,
    last_occurred_at: datetime,
    last_source: str,
    last_cost_id: UUID,
    horizon_at_issue: datetime,
) -> str:
    """Encode the opaque v1 cursor (key + frozen horizon)."""
    payload = {
        "last_occurred_at": rfc3339_utc(value=last_occurred_at),
        "last_source": last_source,
        "last_cost_id": str(last_cost_id),
        "horizon_at_issue": rfc3339_utc(value=horizon_at_issue),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cost_export_cursor(*, cursor: str | None) -> _CursorPayload | None:
    """Decode a v1 cursor, or return ``None`` when the request has no cursor."""
    if cursor is None or cursor == "":
        return None
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CostExportCursorError("cursor is not valid v1 encoding") from error
    if not isinstance(payload, dict):
        raise CostExportCursorError("cursor payload is not an object")
    try:
        return _CursorPayload(
            last_occurred_at=parse_rfc3339(value=str(payload["last_occurred_at"])),
            last_source=str(payload["last_source"]),
            last_cost_id=UUID(str(payload["last_cost_id"])),
            horizon_at_issue=parse_rfc3339(value=str(payload["horizon_at_issue"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CostExportCursorError(
            "cursor fields are incomplete or invalid"
        ) from error


def parse_cost_export_bind(*, bind: str) -> tuple[str | None, int | None, str | None]:
    """Parse ``host:port``, ``[ipv6]:port``, or ``unix:/path`` into uvicorn args.

    Returns ``(host, port, uds)`` with unused members set to ``None``.
    """
    value = bind.strip()
    if value.startswith("unix:"):
        path = value.removeprefix("unix:")
        if not path:
            raise CostExportConfigError("unix export bind is missing a socket path")
        return None, None, path
    if value.startswith("[") and "]:" in value:
        host, _, port_text = value[1:].partition("]:")
        return host, _parse_port(port_text=port_text), None
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise CostExportConfigError(
            "export bind must be host:port, [ipv6]:port, or unix:/path"
        )
    return host, _parse_port(port_text=port_text), None


def spine_deployment_id(*, engine: Engine) -> UUID:
    """Return the single spine deployment id, or raise if D50 is violated."""
    with engine.connect() as connection:
        rows = list(connection.execute(text("SELECT deployment_id FROM deployments")))
    if len(rows) != 1:
        raise CostExportConfigError(
            f"cost export requires exactly one deployments row, found {len(rows)}"
        )
    return UUID(str(rows[0][0]))


def _parse_port(*, port_text: str) -> int:
    """Parse a TCP port and reject out-of-range values."""
    try:
        port = int(port_text)
    except ValueError as error:
        raise CostExportConfigError("export bind port is not an integer") from error
    if port < 1 or port > 65_535:
        raise CostExportConfigError("export bind port is out of range")
    return port


def _as_utc(*, value: datetime) -> datetime:
    """Normalize a timestamp to UTC, treating naive values as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _receipt_from_row(*, row: dict[str, object]) -> CostExportReceipt:
    """Project one view row onto the frozen receipt model."""
    cost_usd = row["cost_usd"]
    return CostExportReceipt(
        cost_id=_as_uuid(value=row["cost_id"]),
        deployment_id=_as_uuid(value=row["deployment_id"]),
        source=_source(value=row["source"]),
        work_id=_as_uuid(value=row["work_id"]),
        stage=_optional_str(value=row["stage"]),
        lane=_optional_str(value=row["lane"]),
        attempt=_optional_int(value=row["attempt"]),
        surface=_optional_str(value=row["surface"]),
        call_key=str(row["call_key"]),
        outcome=str(row["outcome"]),
        model_name=_optional_str(value=row["model_name"]),
        tokens_in=_optional_int(value=row["tokens_in"]),
        tokens_out=_optional_int(value=row["tokens_out"]),
        cost_usd=None if cost_usd is None else Decimal(str(cost_usd)),
        latency_ms=_optional_int(value=row["latency_ms"]),
        occurred_at=_as_utc(value=row["occurred_at"]),  # type: ignore[arg-type]
    )


def _source(*, value: object) -> Literal["worker", "surface"]:
    """Fail closed if the view emits a source outside the v1 allowlist."""
    if value == "worker":
        return "worker"
    if value == "surface":
        return "surface"
    raise ValueError(f"v_cost_receipts emitted unknown source {value!r}")


def _as_uuid(*, value: object) -> UUID:
    """Coerce a driver uuid or string to ``UUID``."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_str(*, value: object) -> str | None:
    """Return ``None`` for SQL NULL, otherwise the string form."""
    return None if value is None else str(value)


def _optional_int(*, value: object) -> int | None:
    """Return ``None`` for SQL NULL, otherwise ``int``."""
    return None if value is None else int(value)  # type: ignore[arg-type]


def _last_key(
    *, receipts: tuple[CostExportReceipt, ...], incoming: _CursorPayload | None
) -> tuple[datetime, str, UUID]:
    """Last returned key, else incoming key, else the zero key."""
    if receipts:
        last = receipts[-1]
        return last.occurred_at, last.source, last.cost_id
    if incoming is not None:
        return incoming.last_occurred_at, incoming.last_source, incoming.last_cost_id
    return _ZERO_OCCURRED_AT, "", _ZERO_COST_ID


_SELECT_RECEIPTS = text(
    """
    SELECT
      source, cost_id, deployment_id, work_id, stage, lane, attempt, surface,
      call_key, outcome, model_name, tokens_in, tokens_out, cost_usd,
      latency_ms, occurred_at
    FROM v_cost_receipts
    WHERE deployment_id = CAST(:deployment_id AS uuid)
      AND occurred_at <= CAST(:upper_bound AS timestamptz)
      AND (
        CAST(:use_key AS boolean) = false
        OR (occurred_at, source, cost_id) > (
          CAST(:last_occurred_at AS timestamptz),
          CAST(:last_source AS text),
          CAST(:last_cost_id AS uuid)
        )
      )
    ORDER BY occurred_at, source, cost_id
    LIMIT CAST(:limit AS integer)
    """
)

_SELECT_METER_STATE = text(
    """
    SELECT persist_failures, scope_missing
    FROM surface_cost_meter_state
    WHERE deployment_id = CAST(:deployment_id AS uuid)
    """
)
