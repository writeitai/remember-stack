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
    #: Whether running this operation changes the memory.
    #:
    #: ``None`` means the operation has not said. The perimeter treats that as
    #: mutating, so a read-only credential is refused it: an operation nobody
    #: classified is an operation nobody has checked, and guessing "harmless"
    #: is the guess that costs something when it is wrong.
    mutates: bool | None = None
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


#: The processing states a document version can be in (`document_status`).
#:
#: Named once, here, because the HTTP port, the read model and the filter all
#: have to agree on it: a port that said `str` while the implementation said
#: this would be a type error at the seam, and widening the port to `str`
#: would let an unvalidated status reach the query.
DocumentStatus = Literal[
    "ingesting", "converting", "structuring", "ready", "failed", "deleted"
]


class DocumentVersionSummary(BaseModel):
    """The state of one observed snapshot of a document.

    ``status`` is the version's own processing state, not a judgement about
    the document: a lineage whose newest version is ``failed`` may still be
    serving an older one that is ``ready``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_id: UUID
    version_no: int
    status: DocumentStatus
    ingested_at: datetime
    #: Present only on ``failed``. The engine's own message, not a paraphrase.
    error: str | None = None


class DocumentSummary(BaseModel):
    """One document lineage, with the newest snapshot the engine has observed.

    ``latest`` is the highest ``version_no`` in the lineage, which is
    deliberately not the same as the lineage's *current* version. The current
    pointer only moves once a snapshot finishes processing, so a document
    whose first version is still converting — or whose newest version failed —
    has no current version at all. Keying this on the current pointer would
    make exactly the documents somebody is worried about disappear from the
    list, which is the opposite of what an intake view is for.

    ``serving`` says whether a *ready* snapshot exists to answer questions
    from, so the two facts stay separable: "the newest upload failed" and
    "there is nothing here to search" are different situations and a customer
    needs to tell them apart.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: UUID
    #: Best-effort human name. May be absent for sources that never supplied one.
    title: str | None = None
    #: Connector kind: ``upload``, ``google_drive``, ``url``, ….
    source_kind: str
    #: Where it came from, when the source has a location worth showing.
    source_uri: str | None = None
    first_seen_at: datetime
    latest: DocumentVersionSummary
    #: True when some version of this lineage is ``ready`` to be searched.
    serving: bool
    #: Stages the pipeline deliberately did not run for the newest version.
    #:
    #: A skip is not a failure and not a success — it is work that was
    #: considered and declined, and it changes what the document can answer.
    #: A version whose extraction was skipped is `ready` and searchable as
    #: text while contributing no claims, so a screen that reported only the
    #: status would call it fine and leave somebody wondering why it never
    #: shows up in answers. Empty for the overwhelming majority of documents.
    skipped_stages: tuple[str, ...] = ()


class DocumentPage(BaseModel):
    """One page of the document inventory.

    ``cursor`` is opaque and absent on the last page. Callers must not
    construct one: it encodes the sort position, and an invented value would
    silently skip or repeat documents rather than fail.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: tuple[DocumentSummary, ...]
    cursor: str | None = None


class SearchRequest(BaseModel):
    """A search, with the terms in the body rather than the request line.

    The query is the customer's own words — often the most sensitive string in
    the whole exchange. A URL is not a private place: it is written to access
    logs, kept by proxies, retained in browser history, and attached to
    referrers. So the search surface takes a body, and the terms never appear
    in a request line (D59).

    The ``GET`` forms remain for existing clients, which reach the deployment
    over a private path. A browser does not.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4096)
    k: int = Field(default=10, ge=1, le=400)
    channel: Literal["semantic", "bm25"] = "semantic"


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
    document_binding_generation: str | None = Field(
        default=None,
        description=(
            "Current bounded document-entity projection generation. NULL means "
            "document-local exact T0 replay is disabled."
        ),
    )
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
    document_binding_generation: str | None = Field(
        default=None,
        description="Current document-local entity binding projection generation.",
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
