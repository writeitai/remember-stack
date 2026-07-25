"""Meaningful invariants on shared immutable provider-boundary values."""

from decimal import Decimal

from pydantic import BaseModel
from pydantic import SecretBytes
from pydantic import ValidationError
import pytest

from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ObjectKey
from rememberstack.model import PerimeterCredential
from rememberstack.model import ProviderCallUsage
from rememberstack.model import PublishedMounts
from rememberstack.model import SelectionDropReason
from rememberstack.model import SelectionOutcome
from rememberstack.model import SelectionResponse
from rememberstack.model import SelectionVerdict


class _Output(BaseModel):
    """Small structured output used to prove response/usage pairing."""

    answer: str


def test_generated_response_keeps_exact_decimal_provider_cost() -> None:
    """Carry provider accounting beside a validated structured output."""
    response = GeneratedResponse(
        output=_Output(answer="ok"),
        usage=ProviderCallUsage(
            model_name="generation-model",
            tokens_in=7,
            tokens_out=2,
            cost_usd=Decimal("0.000123"),
            latency_ms=4,
        ),
    )

    assert response.output.answer == "ok"
    assert response.usage.cost_usd == Decimal("0.000123")


def test_embedding_response_rejects_mixed_dimensions() -> None:
    """Reject malformed provider batches before vectors reach application logic."""
    with pytest.raises(ValidationError):
        EmbeddingResponse(
            vectors=((1.0, 2.0), (3.0,)),
            usage=ProviderCallUsage(
                model_name="embedding-model",
                tokens_in=1,
                tokens_out=0,
                cost_usd=Decimal(0),
                latency_ms=0,
            ),
        )


def test_object_key_is_non_empty_and_frozen() -> None:
    """Keep immutable storage identity explicit at the byte/object-key boundary."""
    key = ObjectKey(root="snapshots/valid/revision")

    with pytest.raises(ValidationError):
        ObjectKey(root="")

    with pytest.raises(ValidationError):
        key.root = "replacement"  # type: ignore[misc]


def test_published_mounts_cannot_claim_a_writable_view() -> None:
    """Make the D51 read-only mount invariant a validated boundary value."""
    with pytest.raises(ValidationError):
        PublishedMounts.model_validate(
            {
                "deployment_id": "00000000-0000-0000-0000-000000000001",
                "p3": "mount://p3",
                "artifacts": "mount://artifacts",
                "raw": "mount://raw",
                "knowledge": "mount://knowledge",
                "read_only": False,
            }
        )


def test_perimeter_credential_redacts_secret_bytes() -> None:
    """Keep credential bytes out of model reprs at the auth boundary."""
    credential = PerimeterCredential(
        scheme="api-key", value=SecretBytes(b"must-not-appear")
    )

    assert "must-not-appear" not in repr(credential)


def test_selection_outcome_matches_the_database_vocabulary() -> None:
    """Reject provider prose before a selection decision reaches PostgreSQL."""
    valid = SelectionResponse.model_validate(
        {
            "candidates": [
                {
                    "source_span": "How are you?",
                    "outcome": "drop_question",
                    "protected_class": None,
                }
            ]
        }
    )

    assert valid.candidates[0].drop_reason is SelectionDropReason.QUESTION
    assert valid.candidates[0].verdict is SelectionVerdict.DROP
    with pytest.raises(ValidationError):
        SelectionResponse.model_validate(
            {
                "candidates": [
                    {
                        "source_span": "How are you?",
                        "outcome": "drop_question (the speaker asks a question)",
                        "protected_class": None,
                    }
                ]
            }
        )


def test_every_drop_reason_has_exactly_one_outcome() -> None:
    """A new drop reason without an outcome would be unreportable by Selection."""
    encoded = {
        outcome.value.removeprefix("drop_")
        for outcome in SelectionOutcome
        if outcome.value.startswith("drop_")
    }
    assert encoded == {reason.value for reason in SelectionDropReason}


@pytest.mark.parametrize(
    ("outcome", "expected_reason"),
    (
        ("keep", None),
        ("keep_flagged", None),
        ("drop_opinion", SelectionDropReason.OPINION),
        # Multi-underscore reasons are where a naive prefix split would corrupt
        # the derived value, so they are asserted exactly rather than by shape.
        ("drop_no_info", SelectionDropReason.NO_INFO),
        ("drop_references_boilerplate", SelectionDropReason.REFERENCES_BOILERPLATE),
    ),
)
def test_outcome_round_trips_to_verdict_and_reason(
    outcome: str, expected_reason: SelectionDropReason | None
) -> None:
    """Every outcome yields a consistent verdict/reason pair by construction.

    The pair can no longer disagree: a keep carrying a drop reason, or a drop
    missing one, is unrepresentable rather than merely invalid, so the provider
    cannot emit the combinations that previously failed validation after the
    fact.
    """
    candidate = SelectionResponse.model_validate(
        {"candidates": [{"source_span": "A statement.", "outcome": outcome}]}
    ).candidates[0]

    assert candidate.drop_reason is expected_reason
    if expected_reason is None:
        assert candidate.verdict is not SelectionVerdict.DROP
    else:
        assert candidate.verdict is SelectionVerdict.DROP


@pytest.mark.parametrize(
    "outcome", ("drop", "question", "keep_flagged_advice", "", "DROP_OPINION")
)
def test_selection_rejects_outcomes_outside_the_vocabulary(outcome: str) -> None:
    """Only the exact controlled values are accepted."""
    with pytest.raises(ValidationError):
        SelectionResponse.model_validate(
            {"candidates": [{"source_span": "A statement.", "outcome": outcome}]}
        )
