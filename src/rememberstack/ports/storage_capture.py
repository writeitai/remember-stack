"""Required operational persistence for immutable D152 evidence.

This storage is operator audit_operations/COGS outside the measured customer
set. There is no in-memory/default implementation or financial authority.
Each write must create immutable canonical bytes durably, compare an existing
object byte-for-byte on replay and refuse changed bytes. Completion is last.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from rememberstack.model.object_store import ObjectKey


class StorageCaptureObjectStore(Protocol):
    """Ordinary exact-store names/bodies, never historical inventory authority."""

    def capture_store_identity(self) -> str:
        """Return this actual configured bucket/filesystem, not a caller alias."""
        ...

    def iter_capture_key_candidates(self) -> Iterator[str]:
        """Stream ordinary current key names for matching/classification only."""
        ...

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Read a referenced immutable manifest under its normal store authority."""
        ...


class StorageCaptureEvidenceStore(Protocol):
    """Exactly one capture's fixed records, without listing or mutable state."""

    def read_manifest(self, *, capture_id: str) -> bytes | None:
        """Read manifest.json, the last-written durable completion barrier."""
        ...

    def read_native_witness(self, *, capture_id: str) -> bytes | None:
        """Read the original immutable native-witness.json for completed replay."""
        ...

    def read_provider_manifest(self, *, capture_id: str) -> bytes | None:
        """Read the original immutable provider-manifest.json for replay."""
        ...

    def read_page(self, *, capture_id: str, page_ordinal: str) -> bytes | None:
        """Read exactly pages/{page_ordinal}.json, without a prefix listing."""
        ...

    def persist_native_evidence(
        self, *, capture_id: str, witness: bytes, allocations: bytes, profile: bytes
    ) -> None:
        """Durably create native-witness.json, native-allocations.json, profile.json."""
        ...

    def persist_provider_manifest(self, *, capture_id: str, body: bytes) -> None:
        """Durably create provider-manifest.json before publishing completion."""
        ...

    def persist_provider_page(
        self, *, capture_id: str, page_ordinal: str, body: bytes
    ) -> None:
        """Retain provider-pages/{ordinal}.json independently of provider expiry."""
        ...

    def persist_page(self, *, capture_id: str, page_ordinal: str, body: bytes) -> None:
        """Durably create pages/{page_ordinal}.json; it alone is not completion."""
        ...

    def complete(self, *, capture_id: str, manifest: bytes) -> None:
        """Create manifest.json last, after all exact pages/evidence are durable."""
        ...
