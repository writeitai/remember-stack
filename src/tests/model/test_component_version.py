"""Boundary contracts for component-version catalog values and errors."""

from datetime import datetime
from datetime import UTC
from uuid import UUID

from pydantic import ValidationError
import pytest

from rememberstack.model import ComponentVersionConflictError
from rememberstack.model import ComponentVersionError
from rememberstack.model import ComponentVersionNotFoundError
from rememberstack.model import ComponentVersionRecord
from rememberstack.model import PipelineComponent
from rememberstack.model import RegisterComponentVersionInput
from rememberstack.model import RegisterComponentVersionResult

_DEPLOYMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
_PROMPT_HASH = "a" * 64
_INPUT_VALUES: dict[str, object] = {
    "deployment_id": _DEPLOYMENT_ID,
    "component": PipelineComponent.EMBEDDER,
    "version": "embedder-v1",
    "model_name": "text-embedding-3-large",
    "prompt_hash": _PROMPT_HASH,
    "embedding_dim": 3072,
    "params": {"dimensions": 3072},
    "notes": "Pinned production embedder",
}


def test_input_has_exact_immutable_extra_forbidden_shape() -> None:
    """Expose exactly eight explicit fields and preserve supplied text verbatim."""
    component_input = RegisterComponentVersionInput.model_validate(
        {**_INPUT_VALUES, "version": "  Embedder-V1  ", "notes": "  exact notes  "}
    )

    assert tuple(RegisterComponentVersionInput.model_fields) == tuple(_INPUT_VALUES)
    assert component_input.version == "  Embedder-V1  "
    assert component_input.notes == "  exact notes  "
    with pytest.raises(ValidationError):
        RegisterComponentVersionInput.model_validate(
            {**_INPUT_VALUES, "unexpected": True}
        )
    with pytest.raises(ValidationError):
        component_input.version = "replacement"  # type: ignore[misc]


def test_input_requires_explicit_identity_and_rejects_empty_version() -> None:
    """Reject missing key fields and the only required non-empty string."""
    for field in ("deployment_id", "component", "version"):
        values = dict(_INPUT_VALUES)
        del values[field]
        with pytest.raises(ValidationError):
            RegisterComponentVersionInput.model_validate(values)

    with pytest.raises(ValidationError):
        RegisterComponentVersionInput.model_validate({**_INPUT_VALUES, "version": ""})


def test_pipeline_component_is_the_exact_closed_twenty_four_member_enum() -> None:
    """Accept every schema enum member and reject an undeclared component."""
    expected = (
        "ingester",
        "converter",
        "blockizer",
        "structurer",
        "crossreferencer",
        "chunker",
        "context_prefixer",
        "embedding_input_policy",
        "extractor",
        "grounder",
        "resolver",
        "normalizer",
        "adjudicator",
        "embedder",
        "fact_labeler",
        "profile_summarizer",
        "community_detector",
        "snapshot_builder",
        "knowledge_planner",
        "knowledge_writer",
        "knowledge_reflector",
        "knowledge_linter",
        "judge",
        "forgetter",
    )

    assert tuple(component.value for component in PipelineComponent) == expected
    for component in expected:
        parsed = RegisterComponentVersionInput.model_validate(
            {
                "deployment_id": _DEPLOYMENT_ID,
                "component": component,
                "version": f"{component}-v1",
                "embedding_dim": 1536 if component == "embedder" else None,
            }
        )
        assert parsed.component.value == component
    with pytest.raises(ValidationError):
        RegisterComponentVersionInput.model_validate(
            {**_INPUT_VALUES, "component": "unknown_component"}
        )


@pytest.mark.parametrize(
    "prompt_hash", ("a" * 63, "a" * 65, "A" * 64, ("a" * 63) + "g", ("a" * 64) + "\n")
)
def test_prompt_hash_requires_exact_lowercase_sha256(prompt_hash: str) -> None:
    """Reject every hash that is not exactly 64 lowercase hexadecimal characters."""
    with pytest.raises(ValidationError):
        RegisterComponentVersionInput.model_validate(
            {**_INPUT_VALUES, "prompt_hash": prompt_hash}
        )


def test_embedding_dimension_is_embedder_only() -> None:
    """Accept the dimension for embedder and reject it for every other component."""
    assert (
        RegisterComponentVersionInput.model_validate(_INPUT_VALUES).embedding_dim
        == 3072
    )
    for component in PipelineComponent:
        if component is PipelineComponent.EMBEDDER:
            continue
        with pytest.raises(ValidationError):
            RegisterComponentVersionInput.model_validate(
                {**_INPUT_VALUES, "component": component}
            )


def test_params_default_is_an_independent_empty_mapping() -> None:
    """Default each registration to its own empty semantic JSON object."""
    first = RegisterComponentVersionInput(
        deployment_id=_DEPLOYMENT_ID,
        component=PipelineComponent.INGESTER,
        version="ingester-v1",
    )
    second = RegisterComponentVersionInput(
        deployment_id=_DEPLOYMENT_ID,
        component=PipelineComponent.INGESTER,
        version="ingester-v2",
    )

    assert dict(first.params) == {}
    assert dict(second.params) == {}
    assert first.params is not second.params


def test_result_and_record_have_exact_immutable_shapes() -> None:
    """Expose exactly the settled four result and nine resolved-record fields."""
    result = RegisterComponentVersionResult(
        deployment_id=_DEPLOYMENT_ID,
        component=PipelineComponent.EXTRACTOR,
        version="extractor-v1",
        created=True,
    )
    record = ComponentVersionRecord.model_validate(
        {**_INPUT_VALUES, "configured_at": datetime(2026, 7, 18, tzinfo=UTC)}
    )

    assert tuple(RegisterComponentVersionResult.model_fields) == (
        "deployment_id",
        "component",
        "version",
        "created",
    )
    assert tuple(ComponentVersionRecord.model_fields) == (
        "deployment_id",
        "component",
        "version",
        "model_name",
        "prompt_hash",
        "embedding_dim",
        "params",
        "notes",
        "configured_at",
    )
    with pytest.raises(ValidationError):
        result.created = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        record.notes = "replacement"  # type: ignore[misc]


def test_component_version_errors_are_distinguishable_by_type() -> None:
    """Keep conflict and resolution-miss errors distinct under one common base."""
    assert issubclass(ComponentVersionConflictError, ComponentVersionError)
    assert issubclass(ComponentVersionNotFoundError, ComponentVersionError)
    assert not issubclass(ComponentVersionConflictError, ComponentVersionNotFoundError)
