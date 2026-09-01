"""Inventory tests for D61 substrate seams plus D74 store capabilities."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import TypeVar
from uuid import UUID
from uuid import uuid4

from pydantic import BaseModel
from pydantic import SecretBytes

from rememberstack.adapters.selfhost.source_handle import ObjectSourceHandle
from rememberstack.model import AuthenticatedContext
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import KRevision
from rememberstack.model import ModelRequest
from rememberstack.model import ObjectKey
from rememberstack.model import PerimeterCredential
from rememberstack.model import ProviderCallUsage
from rememberstack.model import PublishedMounts
from rememberstack.model import QueueRoute
from rememberstack.model import SourceIdentity
from rememberstack.model import StructuredResponseModel
from rememberstack.model import TelemetryEvent
from rememberstack.model import UTCDateTime
import rememberstack.ports as ports
from rememberstack.ports import AuthPerimeterPort
from rememberstack.ports import KGitRemotePort
from rememberstack.ports import ModelProviderPort
from rememberstack.ports import MountPublisherPort
from rememberstack.ports import ObjectStorePort
from rememberstack.ports import SourceHandlePort
from rememberstack.ports import TaskQueuePort
from rememberstack.ports import TelemetryPort
import rememberstack.ports.auth as auth_module
import rememberstack.ports.forget as forget_module
import rememberstack.ports.git as git_module
import rememberstack.ports.model_provider as model_provider_module
import rememberstack.ports.mounts as mounts_module
import rememberstack.ports.object_store as object_store_module
import rememberstack.ports.postgres_read as postgres_read_module
import rememberstack.ports.purge as purge_module
import rememberstack.ports.queue as queue_module
import rememberstack.ports.source_handle as source_handle_module
import rememberstack.ports.telemetry as telemetry_module

ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)

_PORT_MODULES: tuple[ModuleType, ...] = (
    auth_module,
    forget_module,
    git_module,
    model_provider_module,
    mounts_module,
    object_store_module,
    postgres_read_module,
    queue_module,
    purge_module,
    source_handle_module,
    telemetry_module,
)
_PORT_EXPORTS = {
    "AuthPerimeterPort",
    "ForgetManifestPort",
    "KGitPurgePort",
    "KGitRemotePort",
    "ModelProviderPort",
    "MountPublisherPort",
    "ObjectStorePort",
    "ObjectPurgePort",
    "PostgresReadPoolPort",
    "ProjectionPurgePort",
    "SourceHandlePort",
    "TaskQueuePort",
    "TelemetryPort",
}


class ExampleResponse(BaseModel):
    """Representative schema-constrained response returned by the model fake."""

    answer: str


class FakeObjectStore:
    """Minimal immutable in-memory fake structurally conforming to the object port."""

    def __init__(self) -> None:
        """Initialize an empty object-key map."""
        self.objects: dict[str, bytes] = {}

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Return the bytes stored under the requested key."""
        return self.objects[key.root]

    def open_stream(
        self, *, key: ObjectKey, chunk_bytes: int = 1024 * 1024
    ) -> Iterator[bytes]:
        """Yield the stored bytes in fixed-size chunks."""
        content = self.objects[key.root]
        for offset in range(0, len(content), chunk_bytes):
            yield content[offset : offset + chunk_bytes]

    def read_range(self, *, key: ObjectKey, start: int, end: int) -> bytes:
        """Return the half-open byte interval of the stored bytes."""
        return self.objects[key.root][start:end]

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str | None = None
    ) -> None:
        """Store new bytes and reject replacement of an existing immutable key."""
        if key.root in self.objects:
            raise FileExistsError(key.root)
        self.objects[key.root] = content


class FakeMountPublisher:
    """Minimal four-view fake structurally conforming to the mount port."""

    def publish(self, *, deployment_id: UUID) -> PublishedMounts:
        """Return one portable locator for each required read-only view."""
        return PublishedMounts(
            deployment_id=deployment_id,
            p3="mount://p3",
            artifacts="mount://artifacts",
            raw="mount://raw",
            knowledge="mount://knowledge",
            read_only=True,
        )


class FakeKGitRemote:
    """Minimal single-writer fake structurally conforming to the K remote port."""

    def checkout(self, *, destination: Path) -> KRevision:
        """Return a revision for the requested driver worktree."""
        return KRevision(root=f"checkout:{destination.name}")

    def publish(self, *, worktree: Path) -> KRevision:
        """Return the revision published from the driver worktree."""
        return KRevision(root=f"published:{worktree.name}")


class FakeModelProvider:
    """Minimal schema-validating fake conforming to the combined model port."""

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Construct the caller's declared response type from the rendered prompt."""
        return GeneratedResponse(
            output=response_type.model_validate({"answer": request.prompt}),
            usage=_usage(model_name=request.model),
        )

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return one two-dimensional vector for every input text."""
        return EmbeddingResponse(
            vectors=tuple((float(index), 1.0) for index, _ in enumerate(request.texts)),
            usage=_usage(model_name=request.model),
        )


def _usage(*, model_name: str) -> ProviderCallUsage:
    """Return deterministic accounting for this structural fake."""
    return ProviderCallUsage(
        model_name=model_name,
        tokens_in=0,
        tokens_out=0,
        cost_usd=Decimal(0),
        latency_ms=0,
    )


class FakeTelemetry:
    """Minimal recording fake preserving original exception identities."""

    def __init__(self) -> None:
        """Initialize empty event and exception recordings."""
        self.events: list[TelemetryEvent] = []
        self.exceptions: list[tuple[TelemetryEvent, BaseException]] = []

    def export_event(self, *, event: TelemetryEvent) -> None:
        """Record one structured event."""
        self.events.append(event)

    def export_exception(
        self, *, event: TelemetryEvent, exception: BaseException
    ) -> None:
        """Record the exact exception object without converting or trimming it."""
        self.exceptions.append((event, exception))


class FakeAuthPerimeter:
    """Minimal single-deployment fake structurally conforming to the auth port."""

    def __init__(self, *, deployment_id: UUID) -> None:
        """Bind this perimeter fake to exactly one deployment."""
        self.deployment_id = deployment_id

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Return a deployment principal without adding tenant or role authority."""
        return AuthenticatedContext(
            deployment_id=self.deployment_id, principal=credential.scheme
        )


class FakeTaskQueue:
    """One-operation fake structurally conforming to the D67 queue port."""

    def announce(
        self,
        *,
        processing_id: UUID,
        route_snapshot: QueueRoute,
        not_before_snapshot: UTCDateTime,
    ) -> None:
        """Accept delivery snapshots without assuming authority over their row."""


_object_store_assignment: ObjectStorePort = FakeObjectStore()
_source_handle_assignment: SourceHandlePort = ObjectSourceHandle(
    store=FakeObjectStore(),
    identity=SourceIdentity(
        object_key=ObjectKey("raw/example"),
        content_hash="0" * 64,
        byte_size=0,
        mime="video/mp4",
    ),
)
_mount_assignment: MountPublisherPort = FakeMountPublisher()
_git_assignment: KGitRemotePort = FakeKGitRemote()
_model_assignment: ModelProviderPort = FakeModelProvider()
_telemetry_assignment: TelemetryPort = FakeTelemetry()
_auth_assignment: AuthPerimeterPort = FakeAuthPerimeter(deployment_id=uuid4())
_queue_assignment: TaskQueuePort = FakeTaskQueue()


def _defined_protocols() -> set[type[object]]:
    """Collect Protocol classes defined by the explicit port modules themselves."""
    result: set[type[object]] = set()
    for module in _PORT_MODULES:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and value.__module__ == module.__name__
                and bool(getattr(value, "_is_protocol", False))
            ):
                result.add(value)
    return result


def test_inventory_exports_exactly_thirteen_defined_protocols() -> None:
    """Keep D61/D74 seams plus bounded PostgreSQL read admission explicit."""
    assert set(ports.__all__) == _PORT_EXPORTS
    assert {protocol.__name__ for protocol in _defined_protocols()} == _PORT_EXPORTS
    assert len(_defined_protocols()) == 13


def test_representative_fakes_conform_structurally() -> None:
    """Exercise runtime structural conformance for all seven Protocol seams."""
    assert isinstance(_object_store_assignment, ObjectStorePort)
    assert isinstance(_mount_assignment, MountPublisherPort)
    assert isinstance(_git_assignment, KGitRemotePort)
    assert isinstance(_model_assignment, ModelProviderPort)
    assert isinstance(_telemetry_assignment, TelemetryPort)
    assert isinstance(_auth_assignment, AuthPerimeterPort)
    assert isinstance(_queue_assignment, TaskQueuePort)


def test_object_store_fake_rejects_immutable_key_replacement() -> None:
    """Show the byte/key contract fails rather than overwriting immutable content."""
    store = FakeObjectStore()
    key = ObjectKey(root="raw/deployment/document/version")
    store.write_bytes(key=key, content=b"first")

    try:
        store.write_bytes(key=key, content=b"replacement")
    except FileExistsError:
        pass
    else:
        raise AssertionError("immutable key replacement unexpectedly succeeded")

    assert store.read_bytes(key=key) == b"first"


def test_mount_fake_returns_exact_four_read_only_surfaces() -> None:
    """Exercise the D51 four-surface publication result without mount mechanics."""
    deployment_id = uuid4()
    published = FakeMountPublisher().publish(deployment_id=deployment_id)

    assert published.deployment_id == deployment_id
    assert published.read_only is True
    assert {"p3", "artifacts", "raw", "knowledge"} <= set(PublishedMounts.model_fields)


def test_model_fake_validates_caller_declared_response_schema() -> None:
    """Exercise typed generation and embedding through one provider seam."""
    provider = FakeModelProvider()
    generated = provider.generate(
        request=ModelRequest(model="configured-model", prompt="typed answer"),
        response_type=ExampleResponse,
    )
    embedded = provider.embed(
        request=EmbeddingRequest(
            model="configured-embedding-model", texts=("one", "two")
        )
    )

    assert generated.output == ExampleResponse(answer="typed answer")
    assert generated.usage.model_name == "configured-model"
    assert len(embedded.vectors) == 2


def test_telemetry_fake_preserves_real_exception_object() -> None:
    """Keep the actual exception reachable for Sentry-class capture."""
    exporter = FakeTelemetry()
    event = TelemetryEvent(
        name="worker.failure",
        occurred_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
        attributes=(),
    )
    failure = RuntimeError("visible failure")

    exporter.export_exception(event=event, exception=failure)

    assert exporter.exceptions == [(event, failure)]
    assert exporter.exceptions[0][1] is failure


def test_auth_fake_returns_only_single_deployment_context() -> None:
    """Keep auth at the perimeter without organization or content-role authority."""
    deployment_id = uuid4()
    perimeter = FakeAuthPerimeter(deployment_id=deployment_id)
    context = perimeter.authenticate(
        credential=PerimeterCredential(
            scheme="api-key", value=SecretBytes(b"not-logged")
        )
    )

    assert context.deployment_id == deployment_id
    assert context.principal == "api-key"
    assert set(AuthenticatedContext.model_fields) == {"deployment_id", "principal"}
