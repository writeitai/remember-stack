"""Client-surface values that are safe in the dependency-light base install."""

from datetime import datetime
from typing import Literal
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import JsonValue
from pydantic import model_validator

_SECRET_CONFIGURATION_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "credential",
        "credentials",
        "password",
        "refreshtoken",
        "secret",
        "token",
    }
)


class ConnectorNotFoundError(Exception):
    """A connector id is not present in this deployment."""


class ToolDescriptor(BaseModel):
    """One assured operation and its live implementation identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    input_schema: dict[str, object]
    result_schema: dict[str, object]
    result_contract: str = Field(min_length=1)
    output_grain: str | None
    answer_intent: str
    version: int | None = Field(default=None, ge=1)
    implementation_plan_hash: str | None = Field(
        default=None, min_length=64, max_length=64
    )


class PipelineStageReadiness(BaseModel):
    """One expected document-version stage at the public readiness boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    component_version: str
    status: Literal[
        "missing", "pending", "running", "succeeded", "failed", "dead_letter", "skipped"
    ]
    finished_at: datetime | None = None


class VersionPipelineReadiness(BaseModel):
    """The complete expected continuous pipeline state for one version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_id: UUID
    ready: bool
    stages: tuple[PipelineStageReadiness, ...]


class ReadinessRequirements(BaseModel):
    """The exhaustive capability set a readiness caller may require."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline: bool
    p1: bool
    live_graph: bool
    p3: bool


class CapabilityReadiness(BaseModel):
    """One live capability's required/readiness state and safe reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    required: bool
    ready: bool
    checked_at: datetime
    reason: str
    version: str | None = None
    built_at: datetime | None = None
    published_at: datetime | None = None


class PipelineReadinessReport(BaseModel):
    """Machine-verifiable E/P readiness for a bounded set of versions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ready: bool
    versions: tuple[VersionPipelineReadiness, ...]
    capabilities: dict[
        Literal["pipeline", "p1", "live_graph", "p3"], CapabilityReadiness
    ]
    model_bindings: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Current non-secret serving-process configuration; this is not"
            " processing-time provenance for the requested versions."
        ),
    )
    build_revision: str = Field(
        default="",
        description=(
            "Source revision stamped into the running image at build time."
            " Empty when the image was built without it. Comparing this against"
            " the revision a benchmark prepared with is the only way to know the"
            " serving code is the code under test; a filesystem checkout says"
            " nothing about what the containers actually run."
        ),
    )


class DeploymentBuildInfo(BaseModel):
    """Non-secret identity of the code and model bindings currently serving.

    Available without version ids so a caller can verify provenance *before*
    submitting work, rather than after the pipeline has already processed it
    under whatever image happened to be running.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    build_revision: str = Field(
        default="",
        description=(
            "Source revision stamped into the running image at build time;"
            " empty when the image was built without it."
        ),
    )
    model_bindings: dict[str, str] = Field(
        default_factory=dict,
        description="Current non-secret provider model identities.",
    )


class ConnectorCreate(BaseModel):
    """Deployment-side connector configuration sent by a client.

    ``credential_ref`` names a secret already held by the deployment. Raw
    credentials never become client-surface configuration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    credential_ref: str | None = None

    @model_validator(mode="after")
    def _credentials_are_references(self) -> Self:
        """Reject conventional secret fields at any configuration depth."""
        secret_key = _find_secret_key(self.configuration)
        if secret_key is not None:
            raise ValueError(
                f"configuration field {secret_key!r} looks like a credential;"
                " store it deployment-side and use credential_ref"
            )
        return self


class ConnectorDescriptor(BaseModel):
    """One managed connector, never an instruction to execute it client-side."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connector_id: UUID
    kind: str
    name: str
    status: Literal["active", "paused", "error"]
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    credential_ref: str | None = None
    message: str | None = None


def _find_secret_key(value: object) -> str | None:
    """Return the first conventional credential key in nested JSON-like data."""
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = key.casefold().replace("-", "").replace("_", "")
            if normalized in _SECRET_CONFIGURATION_KEYS:
                return key
            found = _find_secret_key(nested)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_secret_key(nested)
            if found is not None:
                return found
    return None
