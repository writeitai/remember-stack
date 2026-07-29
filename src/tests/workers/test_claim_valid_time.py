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
from rememberstack.workers.e2 import _CLAIMIFY_PROMPT
from rememberstack.workers.e2 import _parse_claim_valid_time
from rememberstack.workers.e2 import _parse_iso_timestamp


def test_rendered_claimify_prompt_requires_anchored_temporal_resolution() -> None:
    """The extraction request makes #158's structured-only rule unmistakable."""
    rendered = _CLAIMIFY_PROMPT.format(
        keeps="- Melanie painted a lake sunrise last year.",
        bundle=(
            "DOCUMENT HEADER: title chat; source upload; date 2023-05-08;"
            " language en\n"
            "TARGET CHUNK:\nMelanie painted a lake sunrise last year."
        ),
    )

    assert "TEMPORAL RESOLUTION IS REQUIRED" in rendered
    assert "regardless of claim form" in rendered
    assert "relative expression inside quoted or attributed text" in rendered
    assert "you MUST resolve the expression" in rendered
    assert "computed absolute time ONLY in those structured" in rendered
    assert "valid-time fields" in rendered
    assert (
        'claim_text="Caroline said: I went to a support group yesterday"' in rendered
    )
    assert "the relative word stays inside the quoted claim_text" in rendered
    assert 'claim_text="painted a lake sunrise last year"' in rendered
    assert "valid_from_iso=2022-01-01" in rendered
    assert "valid_until_iso=2022-12-31" in rendered
    assert "valid_precision=year" in rendered
    assert "valid_from_iso=2023-05-07" in rendered
    assert "valid_from_iso=2023-05-06" in rendered
    assert "If the document has no absolute anchor" in rendered


def test_claim_valid_enums_match_catalog_contract() -> None:
    """Python D41 temporal enums must equal the catalog_contract vocabularies."""
    assert tuple(kind.value for kind in ClaimValidKind) == CLAIM_VALID_KIND_VALUES
    assert (
        tuple(precision.value for precision in ClaimValidPrecision)
        == CLAIM_VALID_PRECISION_VALUES
    )


def test_parse_iso_timestamp_date_only_is_utc_midnight() -> None:
    """A bare ISO date becomes midnight UTC (no local timezone guess)."""
    assert _parse_iso_timestamp(value="2024-05-08") == (
        datetime(2024, 5, 8, tzinfo=UTC),
        True,  # date-only: carries day precision, must never pose as an instant
    )


def test_parse_iso_timestamp_respects_explicit_offset() -> None:
    """An offset-bearing datetime is converted to the equivalent UTC instant."""
    assert _parse_iso_timestamp(value="2024-05-08T14:30:00+02:00") == (
        datetime(2024, 5, 8, 12, 30, tzinfo=UTC),
        False,
    )


def test_parse_iso_timestamp_null_passthrough() -> None:
    """Absent timestamps stay None so optional valid-time remains optional."""
    assert _parse_iso_timestamp(value=None) == (None, False)


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


def test_bare_kind_without_interval_is_normalized_to_null() -> None:
    """A kind with no interval is meaningless and must not land in the row."""
    candidate = CandidateClaim(
        claim_text="Acme exists.",
        source_span="Acme exists.",
        entailment_self_verdict=True,
        valid_kind=ClaimValidKind.EVENT_TIME,
    )
    valid_from, valid_until, precision, kind = _parse_claim_valid_time(
        candidate=candidate
    )
    assert (valid_from, valid_until) == (None, None)
    assert precision is ClaimValidPrecision.UNKNOWN
    assert kind is None


def test_naive_datetime_degrades_instead_of_inventing_utc() -> None:
    """A datetime with no offset must not be assigned an invented timezone."""
    candidate = CandidateClaim(
        claim_text="The meeting happened at 14:30.",
        source_span="at 14:30",
        entailment_self_verdict=True,
        valid_kind=ClaimValidKind.EVENT_TIME,
        valid_from_iso="2024-05-08T14:30:00",
        valid_until_iso="2024-05-08T14:30:00",
        valid_precision=ClaimValidPrecision.INSTANT,
    )
    valid_from, valid_until, precision, kind = _parse_claim_valid_time(
        candidate=candidate
    )
    assert (valid_from, valid_until, kind) == (None, None, None)
    assert precision is ClaimValidPrecision.UNKNOWN


def test_date_only_bounds_cannot_pose_as_an_instant() -> None:
    """A date carries day precision; equal midnights are not an exact instant."""
    candidate = CandidateClaim(
        claim_text="It happened on 8 May 2024.",
        source_span="on 8 May 2024",
        entailment_self_verdict=True,
        valid_kind=ClaimValidKind.EVENT_TIME,
        valid_from_iso="2024-05-08",
        valid_until_iso="2024-05-08",
        valid_precision=ClaimValidPrecision.INSTANT,
    )
    _, _, precision, kind = _parse_claim_valid_time(candidate=candidate)
    assert precision is ClaimValidPrecision.UNKNOWN
    assert kind is None


def test_out_of_range_utc_conversion_degrades_not_raises() -> None:
    """Offset arithmetic at datetime.max must degrade, not fail the claim."""
    candidate = CandidateClaim(
        claim_text="Forever.",
        source_span="Forever.",
        entailment_self_verdict=True,
        valid_kind=ClaimValidKind.PROPOSITION_VALIDITY,
        valid_from_iso="9999-12-31T23:59:59-01:00",
        valid_until_iso=None,
        valid_precision=ClaimValidPrecision.OPEN,
    )
    valid_from, valid_until, precision, kind = _parse_claim_valid_time(
        candidate=candidate
    )
    assert (valid_from, valid_until, kind) == (None, None, None)
    assert precision is ClaimValidPrecision.UNKNOWN


def test_plus_separator_is_not_a_time() -> None:
    """fromisoformat would read 2024-01-01+02:00 as date plus TIME 02:00."""
    candidate = CandidateClaim(
        claim_text="Dated.",
        source_span="Dated.",
        entailment_self_verdict=True,
        valid_kind=ClaimValidKind.EVENT_TIME,
        valid_from_iso="2024-01-01+02:00",
        valid_until_iso="2024-01-01+02:00",
        valid_precision=ClaimValidPrecision.DAY,
    )
    _, _, precision, _ = _parse_claim_valid_time(candidate=candidate)
    assert precision is ClaimValidPrecision.UNKNOWN
