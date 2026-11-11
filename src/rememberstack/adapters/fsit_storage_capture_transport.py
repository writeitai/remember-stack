"""One engine decoder over the fixed provider's canonical batched SSH stream.

The managed runtime owns authenticated process start, finite I/O deadlines and
unconditional cancellation/reaping. This adapter owns protocol interpretation.
All requests are known and launched together before any native Project cut;
their native collectors must run concurrently, within the admitted batch bound.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from threading import Condition
from typing import Annotated
from typing import Literal
from typing import Protocol
from typing import TypeAlias

from pydantic import Field
from pydantic import SecretStr
from pydantic import TypeAdapter

from rememberstack.adapters.fsit_storage_capture import check_provider_manifest
from rememberstack.adapters.fsit_storage_capture import iter_provider_items
from rememberstack.adapters.fsit_storage_capture import provider_canonical_bytes
from rememberstack.adapters.fsit_storage_capture import ProviderManifest
from rememberstack.adapters.fsit_storage_capture import ProviderPage
from rememberstack.adapters.fsit_storage_capture import ProviderRecord
from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.model.storage_capture import Digest


class ProviderCaptureUnavailable(RuntimeError):
    """A non-secret refused/lost original provider operation."""


class ProviderScopeUnavailable(ProviderCaptureUnavailable):
    """One matched non-complete scope result; other original groups survive."""


class RawProviderCaptureProcess(Protocol):
    """Owned finite-deadline byte transport, with no provider truth parser."""

    def read_line(self, *, maximum_bytes: int) -> bytes | None:
        """Return one bounded LF-terminated frame, or None only at EOF."""
        ...

    def check_success(self) -> None:
        """Wait/reap the original child and refuse a nonzero or uncertain exit."""
        ...


class RawProviderCaptureTransport(Protocol):
    """The fixed authenticated command, never a caller-selected shell/host."""

    def open(
        self, *, stdin: bytes
    ) -> AbstractContextManager[RawProviderCaptureProcess]:
        """Start/write stdin before yielding; always close/cancel/reap on exit."""
        ...


@dataclass(frozen=True, kw_only=True)
class ProviderCaptureScope:
    """One actual finite provider grant and its exact non-secret request."""

    request: ProviderRequest
    grant: SecretStr


class _ManifestFrame(ProviderRecord):
    """The sole fixed host's exact manifest frame."""

    capture_id: Digest
    manifest: ProviderManifest
    protocol: Literal["fsit-capture-v1"]
    type: Literal["manifest"]


class _PageFrame(ProviderRecord):
    """The sole fixed host's exact stored-page frame."""

    capture_id: Digest
    page: ProviderPage
    protocol: Literal["fsit-capture-v1"]
    type: Literal["page"]


class _ResultFrame(ProviderRecord):
    """A fixed non-complete scope result, never a completion or ACK."""

    capture_id: Digest
    protocol: Literal["fsit-capture-v1"]
    status: str = Field(min_length=1, max_length=50)
    type: Literal["result"]


class _ErrorFrame(ProviderRecord):
    """The existing envelope refusal, without authority-existence details."""

    code: Literal["invalid_request"]
    protocol: Literal["fsit-capture-v1"]
    type: Literal["error"]


_Frame: TypeAlias = Annotated[
    _ManifestFrame | _PageFrame | _ResultFrame | _ErrorFrame,
    Field(discriminator="type"),
]
_FRAME = TypeAdapter(_Frame)


def _capture_id(*, request: ProviderRequest) -> str:
    """Bind transport correlation to the existing sole D63 request identity."""
    return hashlib.sha256(
        b"fsit-capture-v1\0" + provider_canonical_bytes(record=request)
    ).hexdigest()


def _envelope(*, scopes: tuple[ProviderCaptureScope, ...]) -> bytes:
    """Build only the fixed singleton-operation exact-scope canonical input."""
    if not scopes:
        raise ValueError("provider_batch_empty")
    first = scopes[0].request
    identifiers: set[str] = set()
    requests: list[dict[str, object]] = []
    for scope in scopes:
        request = scope.request
        capture_id = _capture_id(request=request)
        if (
            request.cell_id != first.cell_id
            or request.cut_at_unix_us != first.cut_at_unix_us
            or capture_id in identifiers
        ):
            raise ValueError("provider_batch_scope")
        grant = scope.grant.get_secret_value()
        secret = grant.removeprefix("mtblc_")
        try:
            decoded = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
        except ValueError:
            raise ValueError("provider_batch_grant") from None
        if (
            not grant.startswith("mtblc_")
            or len(decoded) < 32
            or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != secret
        ):
            raise ValueError("provider_batch_grant")
        identifiers.add(capture_id)
        requests.append({"grant": grant, "request": request.model_dump(mode="json")})
    body = json.dumps(
        {"operation": "capture", "protocol": "fsit-capture-v1", "requests": requests},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > 16 << 20:
        raise ValueError("provider_batch_envelope_bound")
    return body


class _PendingProviderCapture:
    """One ordered scope; its native guard is released before waiting here."""

    def __init__(self, *, source: FsitStorageCaptureSource, index: int) -> None:
        """Retain only the shared original process and the exact scope ordinal."""
        self._source = source
        self._index = index
        self._manifest: ProviderManifest | None = None
        self._streamed = False

    def complete(self) -> ProviderManifest:
        """Read one original complete manifest after earlier groups are consumed."""
        if self._manifest is not None:
            return self._manifest
        source = self._source
        with source._condition:
            source._condition.wait_for(
                lambda: source._cancelled or source._next == self._index
            )
            source._check_live()
        frame = source._read_frame()
        expected = source._requests[self._index]
        if isinstance(frame, _ResultFrame) and frame.capture_id == _capture_id(
            request=expected
        ):
            self._finish_group()
            raise ProviderScopeUnavailable("provider_scope_unavailable")
        if (
            not isinstance(frame, _ManifestFrame)
            or frame.capture_id != _capture_id(request=expected)
            or frame.manifest.capture_id != frame.capture_id
            or frame.manifest.request != expected
        ):
            raise ProviderCaptureUnavailable("provider_capture_refused")
        try:
            check_provider_manifest(manifest=frame.manifest)
        except ValueError:
            raise ProviderCaptureUnavailable("provider_manifest_invalid") from None
        self._manifest = frame.manifest
        return frame.manifest

    def iter_pages(self, *, manifest: ProviderManifest) -> Iterator[ProviderPage]:
        """Stream this exact original group once, retaining no whole page set."""
        if self._manifest != manifest or self._streamed:
            raise ProviderCaptureUnavailable("provider_page_group_state")
        source = self._source
        for ordinal in range(int(manifest.page_count)):
            with source._condition:
                source._check_live()
            frame = source._read_frame()
            if (
                not isinstance(frame, _PageFrame)
                or frame.capture_id != manifest.capture_id
                or frame.page.capture_id != manifest.capture_id
                or frame.page.page_ordinal != str(ordinal)
            ):
                raise ProviderCaptureUnavailable("provider_page_frame_context")
            yield frame.page
        self._finish_group()

    def _finish_group(self) -> None:
        """Advance only a fully consumed group, checking the last original exit."""
        source = self._source
        if self._index + 1 == len(source._requests):
            if source._process.read_line(maximum_bytes=source._maximum) is not None:
                raise ProviderCaptureUnavailable("provider_stream_trailing_frame")
            source._process.check_success()
        with source._condition:
            source._check_live()
            self._streamed = True
            source._next += 1
            source._finished = source._next == len(source._requests)
            source._condition.notify_all()

    def check_transport_complete(self) -> None:
        """Wait after native close for all original groups, EOF and process exit."""
        source = self._source
        with source._condition:
            source._condition.wait_for(lambda: source._cancelled or source._finished)
            source._check_live()

    def discard(self) -> None:
        """Drain one failed Project's original group without publishing evidence."""
        if self._streamed:
            return
        try:
            manifest = self.complete()
        except ProviderScopeUnavailable:
            return
        for _ in iter_provider_items(
            manifest=manifest, pages=self.iter_pages(manifest=manifest)
        ):
            pass


class FsitStorageCaptureSource:
    """One already-launched same-cell/same-T provider batch, safely multiplexed."""

    def __init__(
        self,
        *,
        process: RawProviderCaptureProcess,
        scopes: tuple[ProviderCaptureScope, ...],
        maximum_frame_bytes: int,
    ) -> None:
        """Bind only the original owned stream, exact scopes and qualified bound."""
        self._process = process
        self._requests = tuple(scope.request for scope in scopes)
        self._maximum = maximum_frame_bytes
        self._condition = Condition()
        self._next = 0
        self._claimed: set[int] = set()
        self._cancelled = False
        self._finished = False

    @contextmanager
    def capture(self, *, request: ProviderRequest) -> Iterator[_PendingProviderCapture]:
        """Claim without waiting so every native Project view can arm before T."""
        with self._condition:
            self._check_live()
            try:
                index = self._requests.index(request)
            except ValueError:
                raise ProviderCaptureUnavailable(
                    "provider_scope_not_in_batch"
                ) from None
            if index in self._claimed:
                raise ProviderCaptureUnavailable("provider_scope_already_claimed")
            self._claimed.add(index)
        pending = _PendingProviderCapture(source=self, index=index)
        try:
            yield pending
            if not pending._streamed:
                raise ProviderCaptureUnavailable("provider_scope_incomplete")
        except ProviderScopeUnavailable:
            raise
        except ProviderCaptureUnavailable:
            self.cancel()
            raise
        except Exception:
            try:
                pending.discard()
            except BaseException:
                self.cancel()
                raise
            raise
        except BaseException:
            self.cancel()
            raise

    def cancel(self) -> None:
        """Wake all scope waiters; transport exit must close/reap the raw child."""
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def check_complete(self) -> None:
        """Require every original group and successful EOF before batch exit."""
        with self._condition:
            self._check_live()
            if not self._finished:
                raise ProviderCaptureUnavailable("provider_batch_incomplete")

    def _check_live(self) -> None:
        """Refuse all waiters after any shared original operation fails."""
        if self._cancelled:
            raise ProviderCaptureUnavailable("provider_batch_cancelled")

    def _read_frame(self) -> _Frame:
        """Decode exact closed canonical bytes only, without exposing raw input."""
        line = self._process.read_line(maximum_bytes=self._maximum)
        if (
            line is None
            or len(line) > self._maximum
            or not line.endswith(b"\n")
            or b"\n" in line[:-1]
        ):
            raise ProviderCaptureUnavailable("provider_frame_bound_or_eof")
        try:
            frame = _FRAME.validate_json(line[:-1])
        except ValueError:
            raise ProviderCaptureUnavailable("provider_frame_invalid") from None
        if provider_canonical_bytes(record=frame) + b"\n" != line:
            raise ProviderCaptureUnavailable("provider_frame_not_canonical")
        return frame


@contextmanager
def open_provider_capture_batch(
    *,
    transport: RawProviderCaptureTransport,
    scopes: tuple[ProviderCaptureScope, ...],
    max_scopes: int,
    maximum_frame_bytes: int,
) -> Iterator[FsitStorageCaptureSource]:
    """Launch the sole exact envelope before concurrent native Project arming.

    The caller must use one native worker per admitted scope, then leave this
    context before joining failed/cancelled workers: raw transport exit closes
    blocking I/O first. Workers own/finally-close their native sessions. Both
    bounds are required operational admission inputs, never customer quotas.
    """
    if (
        isinstance(max_scopes, bool)
        or max_scopes <= 0
        or len(scopes) > max_scopes
        or isinstance(maximum_frame_bytes, bool)
        or maximum_frame_bytes <= 0
    ):
        raise ValueError("provider_batch_admission_bound")
    stdin = _envelope(scopes=scopes)
    with transport.open(stdin=stdin) as process:
        source = FsitStorageCaptureSource(
            process=process, scopes=scopes, maximum_frame_bytes=maximum_frame_bytes
        )
        try:
            yield source
            source.check_complete()
        finally:
            source.cancel()
