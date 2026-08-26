"""Boundary contracts for deployment bootstrap values and conflicts."""

from uuid import UUID

from pydantic import ValidationError
import pytest

from rememberstack.model import CoreManifestConflictError
from rememberstack.model import DeploymentBootstrapConflictError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DeploymentBootstrapResult
from rememberstack.model import DeploymentConflictError

_INPUT_VALUES: dict[str, object] = {
    "deployment_id": "10000000-0000-0000-0000-000000000001",
    "slug": "personal",
    "name": "Personal memory",
    "description": None,
    "default_language": "cs",
    "raw_bucket": "s3://personal-raw",
    "artifacts_bucket": "s3://personal-artifacts",
    "corpusfs_bucket": "s3://personal-corpusfs",
    "knowledge_repo_uri": None,
}


def test_bootstrap_input_has_exact_immutable_extra_forbidden_shape() -> None:
    """Expose exactly nine explicit inputs and preserve their values verbatim."""
    deployment_input = DeploymentBootstrapInput.model_validate(
        {**_INPUT_VALUES, "name": "  Personal memory  "}
    )

    assert tuple(DeploymentBootstrapInput.model_fields) == tuple(_INPUT_VALUES)
    assert deployment_input.name == "  Personal memory  "
    with pytest.raises(ValidationError):
        DeploymentBootstrapInput.model_validate({**_INPUT_VALUES, "unexpected": True})
    with pytest.raises(ValidationError):
        deployment_input.slug = "replacement"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field",
    (
        "slug",
        "name",
        "default_language",
        "raw_bucket",
        "artifacts_bucket",
        "corpusfs_bucket",
    ),
)
def test_bootstrap_input_rejects_each_empty_required_string(field: str) -> None:
    """Reject an empty value for every required string without trimming inputs."""
    with pytest.raises(ValidationError):
        DeploymentBootstrapInput.model_validate({**_INPUT_VALUES, field: ""})


def test_bootstrap_input_has_no_default_language_or_hidden_required_values() -> None:
    """Require every non-optional profile value instead of supplying fallbacks."""
    for field in (
        "deployment_id",
        "slug",
        "name",
        "default_language",
        "raw_bucket",
        "artifacts_bucket",
        "corpusfs_bucket",
    ):
        values = dict(_INPUT_VALUES)
        del values[field]
        with pytest.raises(ValidationError):
            DeploymentBootstrapInput.model_validate(values)


def test_bootstrap_result_has_exact_immutable_shape() -> None:
    """Expose exactly the five settled result fields."""
    result = DeploymentBootstrapResult(
        deployment_id=UUID("10000000-0000-0000-0000-000000000001"),
        deployment_created=True,
        entity_types_count=8,
        predicates_count=16,
        predicate_signatures_count=0,
    )

    assert tuple(DeploymentBootstrapResult.model_fields) == (
        "deployment_id",
        "deployment_created",
        "entity_types_count",
        "predicates_count",
        "predicate_signatures_count",
    )
    with pytest.raises(ValidationError):
        result.deployment_created = False  # type: ignore[misc]


def test_conflict_types_are_distinguishable_without_message_parsing() -> None:
    """Keep deployment and manifest conflicts distinct under one common base."""
    assert issubclass(DeploymentConflictError, DeploymentBootstrapConflictError)
    assert issubclass(CoreManifestConflictError, DeploymentBootstrapConflictError)
    assert not issubclass(DeploymentConflictError, CoreManifestConflictError)
