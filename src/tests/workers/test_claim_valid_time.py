"""Unit proofs for E2 D41 claim valid-time parsing (#146).

These stay free of the Postgres-gated E2 chain fixtures so they always run
locally and in CI — the catalog insert proof remains in test_e2_chain.py.
"""

from datetime import datetime
from datetime import UTC

from rememberstack.model import CandidateClaim
from rememberstack.model import ClaimValidKind
from rememberstack.model import ClaimValidPrecision
from rememberstack.spine.catalog_contract import CLAIM_VALID_KIND_VALUES
from rememberstack.spine.catalog_contract import CLAIM_VALID_PRECISION_VALUES
from rememberstack.workers.e2 import _parse_claim_valid_time
from rememberstack.workers.e2 import _parse_iso_timestamp


def test_claim_valid_enums_match_catalog_contract() -> None:
    """Python D41 temporal enums must equal the catalog_contract vocabularies."""
    assert tuple(kind.value for kind in ClaimValidKind) == CLAIM_VALID_KIND_VALUES
    assert (
        tuple(precision.value for precision in ClaimValidPrecision)
        == CLAIM_VALID_PRECISION_VALUES
    )


def test_parse_iso_timestamp_date_only_is_utc_midnight() -> None:
    """A bare ISO date becomes midnight UTC (no local timezone guess)."""
    assert _parse_iso_timestamp(value="2024-05-08") == datetime(2024, 5, 8, tzinfo=UTC)


def test_parse_iso_timestamp_respects_explicit_offset() -> None:
    """An offset-bearing datetime is converted to the equivalent UTC instant."""
    assert _parse_iso_timestamp(value="2024-05-08T14:30:00+02:00") == datetime(
        2024, 5, 8, 12, 30, tzinfo=UTC
    )


def test_parse_iso_timestamp_null_passthrough() -> None:
    """Absent timestamps stay None so optional valid-time remains optional."""
    assert _parse_iso_timestamp(value=None) is None


def test_parse_claim_valid_time_malformed_falls_back_without_failing() -> None:
    """A bad model date must not reject the claim — only the temporal fields."""
    candidate = CandidateClaim(
        claim_text="Project Atlas launched in 2024.",
        source_span="Project Atlas launched in 2024",
        entailment_self_verdict=True,
        valid_kind=ClaimValidKind.EVENT_TIME,
        valid_from_iso="not-a-date",
        valid_until_iso="2024-12-31",
        valid_precision=ClaimValidPrecision.YEAR,
    )
    valid_from, valid_until, precision, kind = _parse_claim_valid_time(
        candidate=candidate
    )
    assert valid_from is None
    assert valid_until is None
    assert precision is ClaimValidPrecision.UNKNOWN
    assert kind is None


def test_parse_claim_valid_time_accepts_year_bounds() -> None:
    """A well-formed year interval survives parsing with both ends and kind."""
    candidate = CandidateClaim(
        claim_text="Project Atlas launched in 2024.",
        source_span="Project Atlas launched in 2024",
        entailment_self_verdict=True,
        valid_kind=ClaimValidKind.EVENT_TIME,
        valid_from_iso="2024-01-01",
        valid_until_iso="2024-12-31",
        valid_precision=ClaimValidPrecision.YEAR,
    )
    valid_from, valid_until, precision, kind = _parse_claim_valid_time(
        candidate=candidate
    )
    assert valid_from == datetime(2024, 1, 1, tzinfo=UTC)
    assert valid_until == datetime(2024, 12, 31, tzinfo=UTC)
    assert precision is ClaimValidPrecision.YEAR
    assert kind is ClaimValidKind.EVENT_TIME
