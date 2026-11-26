"""Normalize timestamps at the client and engine request boundaries."""

from datetime import datetime
from datetime import UTC
from typing import Annotated
from typing import TypeAlias

from pydantic import AfterValidator
from pydantic import AwareDatetime
from pydantic import BeforeValidator
from pydantic import Field

OFFSET_REQUIRED = "must be timezone-aware (include Z or a numeric UTC offset)"


def normalize_utc(value: datetime, *, field: str = "timestamp") -> datetime:
    """Convert an aware instant to UTC without guessing a local time zone."""
    if value.utcoffset() is None:
        raise ValueError(f"{field} {OFFSET_REQUIRED}")
    try:
        return value.astimezone(UTC)
    except OverflowError as error:
        raise ValueError(f"{field} is outside the supported UTC date range") from error


def optional_utc(value: datetime | None, *, field: str) -> datetime | None:
    """Normalize an instant when an optional timestamp was supplied."""
    return None if value is None else normalize_utc(value, field=field)


def parse_iso_timestamp(value: str, *, field: str = "timestamp") -> datetime:
    """Parse an ISO 8601 timestamp and normalize it to UTC."""
    try:
        instant = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("z") else value
        )
    except ValueError as error:
        raise ValueError(f"{field} must be a valid ISO 8601 timestamp") from error
    return normalize_utc(instant, field=field)


def _expand_hour_offset(value: object) -> object:
    """Expand an ISO hour-only offset to the hours and minutes Pydantic parses."""
    if (
        isinstance(value, str)
        and len(value) > 13
        and value[-3] in "+-"
        and value[-2:].isdigit()
    ):
        return value + ":00"
    return value


ISODateTime: TypeAlias = Annotated[datetime, BeforeValidator(_expand_hour_offset)]

UTCInstant: TypeAlias = Annotated[
    AwareDatetime,
    BeforeValidator(_expand_hour_offset),
    AfterValidator(normalize_utc),
    Field(json_schema_extra={"x-normalize-utc": True}),
]
"""A request instant with an explicit offset, normalized to UTC."""
