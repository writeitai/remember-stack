"""Synthetic provider wire and explicit disk evidence for real-store tests.

MinIO supplies actual names/bodies; this builder supplies protocol vectors, not
provider-at-T, UTC, deployed-image or commercial qualification.
"""

from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
from typing import Any

from rememberstack.adapters.fsit_storage_capture import check_provider_manifest
from rememberstack.adapters.fsit_storage_capture import provider_canonical_bytes
from rememberstack.adapters.fsit_storage_capture import provider_page_digest
from rememberstack.adapters.fsit_storage_capture import provider_path_identity
from rememberstack.adapters.fsit_storage_capture import provider_record_digest
from rememberstack.adapters.fsit_storage_capture import ProviderItem
from rememberstack.adapters.fsit_storage_capture import ProviderManifest
from rememberstack.adapters.fsit_storage_capture import ProviderPage
from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.adapters.fsit_storage_capture import ProviderStorageFlags
from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.model import ObjectKey

FIXTURES = Path(__file__).parent / "fixtures/storage_capture"


class CaptureStore:
    """Use the actual MinIO bucket as its filesystem binding, without aliases."""

    def __init__(self, store: MinIOObjectStore, filesystem: str) -> None:
        self.store, self.filesystem = store, filesystem

    def capture_store_identity(self) -> str:
        return self.filesystem

    def iter_capture_key_candidates(self) -> Iterator[str]:
        return (key.root for key in self.store.iter_keys(prefix=""))

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        return self.store.read_bytes(key=key)


class DiskEvidence:
    """Explicit immutable fixture files outside the measured stores."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _read(self, capture_id: str, key: str) -> bytes | None:
        path = self.root / capture_id / key
        return path.read_bytes() if path.exists() else None

    def _put(self, capture_id: str, key: str, body: bytes) -> None:
        path = self.root / capture_id / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            assert path.read_bytes() == body
        else:
            path.write_bytes(body)

    def read_manifest(self, *, capture_id: str) -> bytes | None:
        return self._read(capture_id, "manifest.json")

    def read_native_witness(self, *, capture_id: str) -> bytes | None:
        return self._read(capture_id, "native-witness.json")

    def read_provider_manifest(self, *, capture_id: str) -> bytes | None:
        return self._read(capture_id, "provider-manifest.json")

    def read_page(self, *, capture_id: str, page_ordinal: str) -> bytes | None:
        return self._read(capture_id, f"pages/{page_ordinal}.json")

    def persist_native_evidence(
        self, *, capture_id: str, witness: bytes, allocations: bytes, profile: bytes
    ) -> None:
        for key, body in (
            ("native-witness", witness),
            ("native-allocations", allocations),
            ("profile", profile),
        ):
            self._put(capture_id, key + ".json", body)

    def persist_provider_manifest(self, *, capture_id: str, body: bytes) -> None:
        self._put(capture_id, "provider-manifest.json", body)

    def persist_provider_page(
        self, *, capture_id: str, page_ordinal: str, body: bytes
    ) -> None:
        self._put(capture_id, f"provider-pages/{page_ordinal}.json", body)

    def persist_page(self, *, capture_id: str, page_ordinal: str, body: bytes) -> None:
        self._put(capture_id, f"pages/{page_ordinal}.json", body)

    def complete(self, *, capture_id: str, manifest: bytes) -> None:
        self._put(capture_id, "manifest.json", manifest)


class SyntheticProvider:
    """One fixed vector built before the cut; no claim to provider qualification."""

    def __init__(
        self, *, request: ProviderRequest, stores: dict[str, CaptureStore]
    ) -> None:
        body = provider_canonical_bytes(record=request)
        capture_id = hashlib.sha256(b"fsit-capture-v1\0" + body).hexdigest()
        self.keys: dict[tuple[str, str], str] = {}
        items: list[ProviderItem] = []
        for role, store in stores.items():
            for name in store.iter_capture_key_candidates():
                content = store.read_bytes(key=ObjectKey(name))
                path = provider_path_identity(
                    capture_id=capture_id,
                    filesystem_id=store.filesystem,
                    relative_path=name,
                )
                self.keys[(role, name)] = path
                items.append(
                    ProviderItem(
                        alias_group=None,
                        entry_kind="regular",
                        filesystem_id=store.filesystem,
                        inline_bytes=str(len(content)),
                        logical_bytes=str(len(content)),
                        path_id=path,
                        storage_flags=ProviderStorageFlags(
                            chunk_backed=False,
                            chunk_manifest=False,
                            inline_content=bool(content),
                            metadata_gzip=False,
                            remote_backed=False,
                        ),
                        version_id=hashlib.sha256(content).hexdigest(),
                    )
                )
        items.sort(key=lambda item: (item.filesystem_id, item.path_id))
        self.pages: list[ProviderPage] = []
        for index in range(0, len(items), 1024):
            chunk = tuple(items[index : index + 1024])
            page = ProviderPage(
                capture_id=capture_id,
                item_count=str(len(chunk)),
                items=chunk,
                page_digest="0" * 64,
                page_ordinal=str(len(self.pages)),
                protocol="fsit-page-v1",
            )
            self.pages.append(
                page.model_copy(update={"page_digest": provider_page_digest(page=page)})
            )
        vector = json.loads((FIXTURES / "fsit_bc8482ab_manifest.json").read_bytes())[
            "manifest"
        ]
        delta = int(request.cut_at_unix_us) - int(vector["request"]["cut_at_unix_us"])

        def shift(record: dict[str, Any]) -> None:
            for key, value in record.items():
                if isinstance(value, dict):
                    shift(value)
                elif key.endswith("_unix_us"):
                    record[key] = str(int(value) + delta)

        shift(vector["native_witness"])
        vector["native_witness"]["cell_id"] = request.cell_id
        resource = vector["resources"][0]
        scope = request.model_dump(mode="json", exclude={"cut_at_unix_us", "protocol"})
        vector.update(
            capture_id=capture_id,
            request=request.model_dump(mode="json"),
            expires_at_unix_us=str(int(request.cut_at_unix_us) + 72 * 3600 * 1_000_000),
            item_count=str(len(items)),
            page_count=str(len(self.pages)),
            pages=[
                {
                    key: page.model_dump()[key]
                    for key in ("item_count", "page_digest", "page_ordinal")
                }
                for page in self.pages
            ],
            scope_digest=hashlib.sha256(
                b"fsit-scope-v1\0"
                + json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            sets=[
                {
                    "filesystem_id": filesystem,
                    "entry_count": str(
                        sum(item.filesystem_id == filesystem for item in items)
                    ),
                    "root_materialized": any(
                        item.filesystem_id == filesystem for item in items
                    ),
                }
                for filesystem in request.filesystem_ids
            ],
            resources=[
                {
                    **resource,
                    "filesystem_id": filesystem,
                    "retained_bytes": "0",
                    "sealed_bytes": "0",
                    "pending_bytes": "0",
                }
                for filesystem in request.filesystem_ids
            ],
        )
        manifest = ProviderManifest.model_validate_json(json.dumps(vector))
        manifest = manifest.model_copy(
            update={
                "native_witness_digest": provider_record_digest(
                    domain="fsit-witness-v1", record=manifest.native_witness
                )
            }
        )
        self.manifest = manifest.model_copy(
            update={
                "manifest_digest": provider_record_digest(
                    domain="fsit-manifest-v1", record=manifest, omit="manifest_digest"
                )
            }
        )
        check_provider_manifest(manifest=self.manifest)
        self.completed = False

    def complete(self) -> ProviderManifest:
        return self.manifest

    def iter_pages(self, *, manifest: ProviderManifest) -> Iterator[ProviderPage]:
        assert manifest == self.manifest
        return iter(self.pages)

    def check_transport_complete(self) -> None:
        self.completed = True
