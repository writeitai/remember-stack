"""Unit proofs for E2 D41 claim valid-time parsing (#146).

These stay free of the Postgres-gated E2 chain fixtures so they always run
locally and in CI — the catalog insert proof remains in test_e2_chain.py.
"""

from datetime import datetime
from datetime import UTC
from uuid import UUID

import pytest

from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimValidKind
from rememberstack.model import ClaimValidPrecision
from rememberstack.spine.catalog_contract import CLAIM_VALID_KIND_VALUES
from rememberstack.spine.catalog_contract import CLAIM_VALID_PRECISION_VALUES
from rememberstack.workers.e2 import _CLAIMIFY_PROMPT
from rememberstack.workers.e2 import _header_text
from rememberstack.workers.e2 import _parse_claim_valid_time
from rememberstack.workers.e2 import _parse_iso_timestamp


def test_document_header_keeps_absent_source_time_unknown() -> None:
    """E2 must not replace missing source event time with ingestion time."""
    source = ChunkSource(
        deployment_id=UUID("79000000-0000-0000-0000-000000000001"),
        doc_id=UUID("79000000-0000-0000-0000-000000000002"),
        version_id=UUID("79000000-0000-0000-0000-000000000003"),
        representation_id=UUID("79000000-0000-0000-0000-000000000004"),
        markdown_uri="documents/unknown-time.md",
        blocks_uri="documents/unknown-time.blocks.json",
        title="Unknown time",
        source_kind="upload",
        source_modified_at=None,
        published_at=None,
        language="en",
        structurer_version="test-structure-v1",
        sections=(),
    )

    assert _header_text(source=source) == (
        "title Unknown time; source upload; date unknown; language en"
    )
    dated = source.model_copy(
        update={"source_modified_at": datetime(2023, 5, 1, 13, tzinfo=UTC)}
    )
    assert _header_text(source=dated) == (
        "title Unknown time; source upload; date 2023-05-01T13:00:00+00:00; language en"
    )


def test_rendered_claimify_prompt_requires_anchored_temporal_resolution() -> None:
    """The extraction request resolves relative time into both text and fields."""
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
    assert "WRITE THE RESOLVED DATE INTO claim_text" in rendered
    assert "exactly like a pronoun" in rendered
    assert "valid-time fields" in rendered
    assert (
        'claim_text="Caroline said: I went to a support group on 2023-05-07"'
        in rendered
    )
    assert 'the resolved date replaces "yesterday" even inside the' in rendered
    assert 'claim_text="painted a lake sunrise in 2022"' in rendered
    assert 'claim_text="met the organizer on 2023-05-06"' in rendered
    assert 'added_context=[{text: "on 2023-05-06", source_kind: header}]' in rendered
    assert "keep the relative phrase exactly as\nthe source spoke it" in rendered
    assert "never write a guessed date" in rendered
    assert "unresolved wording stays as spoken; no date is written" in rendered
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


@pytest.mark.parametrize(
    ("kind", "start", "end", "precision"),
    [
        (
            ClaimValidKind.PROPOSITION_VALIDITY,
            "2019-01-01",
            None,
            ClaimValidPrecision.OPEN,
        ),
        (
            ClaimValidKind.EFFECTIVE_PERIOD,
            "2015-01-01",
            "2018-12-31",
            ClaimValidPrecision.YEAR,
        ),
        (
            ClaimValidKind.MEASUREMENT_PERIOD,
            "2023-01-01",
            "2023-12-31",
            ClaimValidPrecision.YEAR,
        ),
        (
            ClaimValidKind.EVENT_TIME,
            "2023-05-08T16:30:00Z",
            "2023-05-08T16:30:00Z",
            ClaimValidPrecision.INSTANT,
        ),
    ],
)
def test_all_taught_window_kinds_survive_the_deterministic_gate(
    kind: ClaimValidKind, start: str, end: str | None, precision: ClaimValidPrecision
) -> None:
    """The four prompt vocabularies produce valid persisted D41 field combinations."""
    candidate = CandidateClaim(
        claim_text="Source assertion.",
        source_span="Source assertion.",
        entailment_self_verdict=True,
        valid_kind=kind,
        valid_from_iso=start,
        valid_until_iso=end,
        valid_precision=precision,
    )
    parsed_start, parsed_end, parsed_precision, parsed_kind = _parse_claim_valid_time(
        candidate=candidate
    )
    assert parsed_kind is kind
    assert parsed_precision is precision
    assert parsed_start == _parse_iso_timestamp(value=start)[0]
    assert parsed_end == _parse_iso_timestamp(value=end)[0]


def test_two_same_day_sources_keep_distinct_temporal_anchors() -> None:
    """Header rendering must retain the exact instant used by relative-hour reasoning."""
    source = ChunkSource(
        deployment_id=UUID(int=1),
        doc_id=UUID(int=2),
        version_id=UUID(int=3),
        representation_id=UUID(int=4),
        markdown_uri="source.md",
        blocks_uri="source.json",
        title="Chat",
        source_kind="upload",
        language="en",
        structurer_version="test",
        sections=(),
        source_modified_at=datetime(2023, 5, 8, 19, 30, tzinfo=UTC),
        published_at=None,
    )
    later = source.model_copy(
        update={"source_modified_at": datetime(2023, 5, 8, 22, tzinfo=UTC)}
    )
    first_prompt = _CLAIMIFY_PROMPT.format(
        keeps="the final ended three hours ago", bundle=_header_text(source=source)
    )
    later_prompt = _CLAIMIFY_PROMPT.format(
        keeps="the final ended three hours ago", bundle=_header_text(source=later)
    )
    assert first_prompt != later_prompt
    assert first_prompt.endswith("date 2023-05-08T19:30:00+00:00; language en")
    assert later_prompt.endswith("date 2023-05-08T22:00:00+00:00; language en")
    fallback = source.model_copy(
        update={"source_modified_at": None, "published_at": source.source_modified_at}
    )
    assert _header_text(source=fallback) == _header_text(source=source)


def test_temporal_prompt_and_schema_explain_kinds_and_precision_independently() -> None:
    """Models receive semantic guidance in both prose and structured field descriptions."""
    description = CandidateClaim.model_fields["valid_kind"].description
    assert description is not None
    for kind in ClaimValidKind:
        assert kind.value in _CLAIMIFY_PROMPT
        assert kind.value in description
    assert "has been CEO since 2019" in _CLAIMIFY_PROMPT
    assert "valid_until_iso=null, valid_precision=open" in _CLAIMIFY_PROMPT
    assert '"this morning" without explicit clock bounds' in _CLAIMIFY_PROMPT
    assert "Explicit absolute dates in the source still resolve" in _CLAIMIFY_PROMPT
    for name in ("valid_kind", "valid_from_iso", "valid_until_iso", "valid_precision"):
        assert CandidateClaim.model_fields[name].description
