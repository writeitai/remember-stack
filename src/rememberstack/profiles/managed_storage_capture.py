"""Engine-owned ordinary managed D152 capture, classification and exact replay.

The provider transport and operational store are required qualified ports. No
native origin follows from a digest/model alone, and no money is moved here.
Private key names exist only in the finite original view/temporary spool.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from typing import Literal
from typing import Protocol

import psycopg
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import SecretStr
from pydantic import TypeAdapter

from rememberstack.adapters.fsit_storage_capture import check_provider_manifest
from rememberstack.adapters.fsit_storage_capture import iter_provider_items
from rememberstack.adapters.fsit_storage_capture import provider_canonical_bytes
from rememberstack.adapters.fsit_storage_capture import provider_path_identity
from rememberstack.adapters.fsit_storage_capture import ProviderItem
from rememberstack.adapters.fsit_storage_capture import ProviderManifest
from rememberstack.adapters.fsit_storage_capture import ProviderPage
from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import CORPUS_TYPES
from rememberstack.core.corpus_export import desired_export_revision
from rememberstack.core.corpus_export import original_name
from rememberstack.core.corpus_export import PIPELINE_ASSET_KINDS
from rememberstack.core.corpus_export import READER_ASSET_KINDS
from rememberstack.core.storage_capture import capture_request_id
from rememberstack.core.storage_capture import check_capture_encoding
from rememberstack.core.storage_capture import iter_capture_pages
from rememberstack.core.storage_capture import manifest_payload_digest
from rememberstack.core.storage_capture import member_payload_digest
from rememberstack.core.storage_capture import membership_set_digest
from rememberstack.core.storage_capture import native_witness_digest
from rememberstack.core.storage_capture import page_payload_digest
from rememberstack.model.conversion import AssetName
from rememberstack.model.object_store import ObjectKey
from rememberstack.model.storage_capture import StorageCaptureManifest
from rememberstack.model.storage_capture import StorageCapturePage
from rememberstack.model.storage_capture import StorageCaptureRequest
from rememberstack.model.storage_capture import StorageComponent
from rememberstack.model.storage_capture import StorageComponentRecord
from rememberstack.model.storage_capture import StorageMember
from rememberstack.model.storage_capture import StorageNativeWitness
from rememberstack.model.storage_capture import StoragePurpose
from rememberstack.model.storage_capture import StorageReferenceMember
from rememberstack.model.storage_capture import StorageResourceObservation
from rememberstack.ports.storage_capture import StorageCaptureEvidenceStore
from rememberstack.ports.storage_capture import StorageCaptureObjectStore
from rememberstack.profiles.storage_capture_profile import build_managed_storage_profile
from rememberstack.profiles.storage_capture_profile import current_class_rules
from rememberstack.profiles.storage_capture_profile import ManagedStorageCaptureProfile
from rememberstack.profiles.storage_capture_profile import profile_bytes
from rememberstack.profiles.storage_capture_profile import (
    STORAGE_CAPTURE_PAGE_MEMBER_LIMIT,
)
from rememberstack.spine.storage_capture_catalog import NativeCorpusDocument
from rememberstack.spine.storage_capture_catalog import NativeStoreReference
from rememberstack.spine.storage_capture_view import NativeCapturedStorageView
from rememberstack.spine.storage_capture_view import NativeStorageCaptureCatalog

_COMPONENTS: tuple[StorageComponent, ...] = (
    "canonical_object",
    "k_repository",
    "p3_origin",
    "postgres_allocated",
)
_MEMBER = TypeAdapter(StorageMember)
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SHA = r"[0-9a-f]{64}"
_CHECKPOINT = re.compile(
    rf"{_UUID}/{_SHA}/conversion-checkpoints/[A-Za-z0-9._-]+/[0-9a-f]{{32}}\.json"
)
_RAW_ATTEMPT = re.compile(rf"{_UUID}/{_SHA}/original(?:\.[^/\x00]+)?")
_REP_ATTEMPT = re.compile(
    rf"{_UUID}/{_SHA}/{_UUID}/(?:document\.md|blocks\.json|conversion\.json|meta\.json|source_map\.json|media/.+)"
)
_UPLOAD = re.compile(r"uploads/[A-Za-z0-9_-]{32}")
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_CORPUS_PARENT = rf"(?:{'|'.join(CORPUS_TYPES)})(?:/{_SLUG}){{0,4}}"
_CORPUS_FOLDER = re.compile(rf"{_CORPUS_PARENT}/{_SLUG}--[0-9a-f]{{24}}")
_CORPUS_KEY = re.compile(rf"(?P<folder>{_CORPUS_FOLDER.pattern})/(?P<file>.+)")
_CORPUS_NAVIGATION = re.compile(rf"(?:{_CORPUS_PARENT}/)?(?:~/[a-z2-7]+/)*_index\.md")
_ASSET_NAME = TypeAdapter(AssetName)


class ManagedStorageCaptureUnavailable(RuntimeError):
    """One non-secret refused attempt; no past reconstruction or zero fallback."""


class PendingProviderCapture(Protocol):
    """One already launched original provider invocation, not a second API."""

    def complete(self) -> ProviderManifest:
        """Wait for the one authenticated finished native manifest."""
        ...

    def iter_pages(self, *, manifest: ProviderManifest) -> Iterator[ProviderPage]:
        """Read exact stored pages using current equivalent scoped authority."""
        ...

    def check_transport_complete(self) -> None:
        """Check the whole original stream/exit after this native view closes."""
        ...


@dataclass(frozen=True, kw_only=True)
class CompletedStorageCapture:
    """An immutable durable result, separate from delivery ACK or settlement."""

    manifest: StorageCaptureManifest
    native_witness: StorageNativeWitness
    provider_manifest: ProviderManifest
    _store: StorageCaptureEvidenceStore = field(repr=False)

    def iter_page_bytes(self) -> Iterator[bytes]:
        """Read exact sorted-key ASCII-escaped canonical D152 page bytes."""
        for ordinal in range(int(self.manifest.page_count)):
            body = self._store.read_page(
                capture_id=self.manifest.capture_id, page_ordinal=str(ordinal)
            )
            if body is None:
                raise ManagedStorageCaptureUnavailable("capture_durable_page_missing")
            page = StorageCapturePage.model_validate_json(body)
            if (
                body != profile_bytes(record=page)
                or page.capture_id != self.manifest.capture_id
                or page.page_ordinal != str(ordinal)
                or page.page_digest != self.manifest.page_digests[ordinal]
                or page.page_digest != page_payload_digest(page=page)
            ):
                raise ManagedStorageCaptureUnavailable(
                    "capture_durable_page_not_canonical"
                )
            yield body


class _Spool:
    """One temporary operator spool; it is never durable completion authority."""

    def __init__(self, *, path: Path) -> None:
        """Index bounded pages on disk instead of retaining the full set in RAM."""
        self.db = sqlite3.connect(path)
        self.db.executescript("""
          CREATE TABLE items(role TEXT,path TEXT,body TEXT,PRIMARY KEY(role,path));
          CREATE TABLE names(role TEXT,path TEXT,name TEXT,PRIMARY KEY(role,path));
          CREATE TABLE refs(role TEXT,path TEXT,class TEXT,native_id TEXT,bytes TEXT,
            required INTEGER,matched INTEGER DEFAULT 0,PRIMARY KEY(role,path,class,native_id));
          CREATE TABLE resources(component TEXT,store TEXT,id TEXT,version TEXT,evidence TEXT,bytes TEXT,
            PRIMARY KEY(store,id,version));
          CREATE TABLE members(kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id));
        """)

    def close(self) -> None:
        """Close before its private temporary directory is removed."""
        self.db.close()

    def member(self, *, member: StorageMember) -> None:
        """Keep each content-free native member once and refuse changed payloads."""
        body = profile_bytes(record=member).decode("utf-8")
        existing = self.db.execute(
            "SELECT body FROM members WHERE kind=? AND id=?",
            (member.kind, member.opaque_id),
        ).fetchone()
        if existing is not None and existing[0] != body:
            raise ManagedStorageCaptureUnavailable("capture_member_conflict")
        self.db.execute(
            "INSERT OR IGNORE INTO members VALUES(?,?,?)",
            (member.kind, member.opaque_id, body),
        )

    def resource(
        self,
        *,
        component: StorageComponent | None,
        store: str,
        identity: str,
        version: str,
        evidence: str,
        measured_bytes: str,
    ) -> None:
        """Count one demonstrated included origin/fork, preserving actual zero."""
        if component is None:
            return
        row = (component, store, identity, version, evidence, measured_bytes)
        old = self.db.execute(
            "SELECT * FROM resources WHERE store=? AND id=? AND version=?",
            (store, identity, version),
        ).fetchone()
        if old is not None and old != row:
            raise ManagedStorageCaptureUnavailable(
                "capture_resource_ownership_conflict"
            )
        self.db.execute("INSERT OR IGNORE INTO resources VALUES(?,?,?,?,?,?)", row)

    def members(self) -> Iterator[StorageMember]:
        """Stream the exact required member order from the operational spool."""
        for (body,) in self.db.execute("SELECT body FROM members ORDER BY kind,id"):
            yield _MEMBER.validate_json(body)


class StorageCaptureCatalog:
    """The one engine operation over real native and qualified provider sources."""

    def __init__(
        self,
        *,
        collector_connect: Callable[[], psycopg.Connection],
        provider: PendingProviderCapture,
        stores: Mapping[str, StorageCaptureObjectStore],
        evidence_store: StorageCaptureEvidenceStore,
        profile: ManagedStorageCaptureProfile,
        identity_key: SecretStr,
    ) -> None:
        """Bind accepted source/profile and ordinary scoped capabilities only."""
        self._profile = profile
        self._native = NativeStorageCaptureCatalog(
            connect=collector_connect, profile=profile.native
        )
        self._pending = provider
        self._stores = dict(stores)
        self._evidence = evidence_store
        self._identity_key = identity_key
        self._rules: dict[str, tuple[str, StoragePurpose, StorageComponent | None]] = {
            name: (store, purpose, component)
            for name, store, purpose, component in current_class_rules()
        }

    def capture_once(
        self, *, request: StorageCaptureRequest
    ) -> CompletedStorageCapture:
        """Execute the batch-admitted live cut; the batch entry owns replay."""
        capture_id = capture_request_id(request=request)
        check_capture_configuration(
            request=request,
            profile=self._profile,
            stores=self._stores,
            identity_key=self._identity_key,
        )
        binding = self._profile.bindings
        provider_request = ProviderRequest(
            cell_id=binding.provider_cell_id,
            cut_at_unix_us=request.cut_at_unix_us,
            filesystem_ids=tuple(
                sorted(row.filesystem_id for row in binding.filesystems)
            ),
            organisation_id=binding.provider_organisation_id,
            protocol="fsit-capture-v1",
        )
        with TemporaryDirectory(prefix="remember-storage-capture-") as temporary:
            spool = _Spool(path=Path(temporary) / "capture.sqlite")
            try:
                pending = self._pending
                with self._native.open_view(request=request) as native:
                    # Entry already proved/released the short native guard.
                    # No provider completion/list/read/delivery runs under it.
                    provider = pending.complete()
                    self._check_provider(provider=provider, expected=provider_request)
                    native.check_continuity()
                    self._stage_provider(
                        spool=spool,
                        pending=pending,
                        provider=provider,
                        capture_id=capture_id,
                    )
                    self._check_native_bindings(native=native)
                    for seed in native.iter_store_references():
                        self._stage_seed(spool=spool, seed=seed, provider=provider)
                    for document in native.iter_corpus_documents():
                        self._stage_corpus_document(
                            spool=spool, seed=document, provider=provider
                        )
                    for terminal in native.iter_terminal_members():
                        spool.member(member=terminal)
                    self._stage_native_allocations(
                        spool=spool, native=native, capture_id=capture_id
                    )
                    self._classify_items(
                        spool=spool,
                        provider=provider,
                        capture_id=capture_id,
                        native=native,
                    )
                    native.check_continuity()
                    witness = native.witness
                    self._evidence.persist_native_evidence(
                        capture_id=capture_id,
                        witness=profile_bytes(record=witness),
                        allocations=profile_bytes(record=native.allocation_sample),
                        profile=profile_bytes(record=self._profile),
                    )
                    native.check_continuity()
                pending.check_transport_complete()
                manifest = self._publish(
                    spool=spool, request=request, witness=witness, provider=provider
                )
                return CompletedStorageCapture(
                    manifest=manifest,
                    native_witness=witness,
                    provider_manifest=provider,
                    _store=self._evidence,
                )
            finally:
                spool.close()

    def _check_native_bindings(self, *, native: NativeCapturedStorageView) -> None:
        """Compare actual original-view configuration, not a caller display name."""
        binding = self._profile.bindings
        if (
            native.identity.database_name != binding.postgres_database_name
            or native.storage.knowledge_repo_uri is not None
        ):
            raise ManagedStorageCaptureUnavailable("capture_native_binding_mismatch")
        for role, root in (
            ("raw", native.storage.raw_bucket),
            ("artifacts", native.storage.artifacts_bucket),
            ("corpus", native.storage.corpusfs_bucket),
        ):
            if root.get_secret_value() != "s3://" + binding.filesystem_for(role=role):
                raise ManagedStorageCaptureUnavailable(
                    "capture_native_store_root_mismatch"
                )

    def _check_provider(
        self, *, provider: ProviderManifest, expected: ProviderRequest
    ) -> None:
        """Bind the one complete provider invocation and accepted native/clock pins."""
        check_provider_manifest(manifest=provider)
        witness = provider.native_witness
        if (
            provider.request != expected
            or witness.profile_digest != self._profile.provider_profile_digest
            or witness.clock.profile_digest
            != self._profile.provider_clock_profile_digest
        ):
            raise ManagedStorageCaptureUnavailable("capture_provider_profile_mismatch")
        if int(witness.guard.released.before_unix_us) < int(
            witness.guard.held_through.before_unix_us
        ) or int(witness.extraction_completed_at_unix_us) < int(
            witness.guard.released.before_unix_us
        ):
            raise ManagedStorageCaptureUnavailable("capture_provider_release_order")

    def _stage_provider(
        self,
        *,
        spool: _Spool,
        pending: PendingProviderCapture,
        provider: ProviderManifest,
        capture_id: str,
    ) -> None:
        """Persist original provider bodies and index their complete immutable set."""
        roles = {
            row.filesystem_id: row.role for row in self._profile.bindings.filesystems
        }

        def pages() -> Iterator[ProviderPage]:
            """Retain each exact original page before it can expire at the provider."""
            for page in pending.iter_pages(manifest=provider):
                self._evidence.persist_provider_page(
                    capture_id=capture_id,
                    page_ordinal=page.page_ordinal,
                    body=provider_canonical_bytes(record=page),
                )
                yield page

        for item in iter_provider_items(manifest=provider, pages=pages()):
            if item.entry_kind == "symlink" or item.alias_group is not None:
                raise ManagedStorageCaptureUnavailable(
                    "capture_managed_link_profile_unsupported"
                )
            spool.db.execute(
                "INSERT INTO items VALUES(?,?,?)",
                (
                    roles[item.filesystem_id],
                    item.path_id,
                    provider_canonical_bytes(record=item).decode(),
                ),
            )
        for resource in provider.resources:
            if int(resource.sealed_bytes) > int(resource.retained_bytes):
                raise ManagedStorageCaptureUnavailable(
                    "capture_provider_resource_subset"
                )
            self._observation(
                spool=spool,
                capture_id=capture_id,
                store=roles[resource.filesystem_id],
                identity=resource.filesystem_id,
                purpose="adapter_metadata",
                evidence=_digest(
                    domain="rememberstack.storage_provider_resource.v1", record=resource
                ),
            )
        self._evidence.persist_provider_manifest(
            capture_id=capture_id, body=provider_canonical_bytes(record=provider)
        )

    def _name(
        self, *, spool: _Spool, provider: ProviderManifest, role: str, key: str
    ) -> str:
        """Derive an exact original-bound path ID without normalising a key."""
        path = provider_path_identity(
            capture_id=provider.capture_id,
            filesystem_id=self._profile.bindings.filesystem_for(role=role),
            relative_path=key,
        )
        old = spool.db.execute(
            "SELECT name FROM names WHERE role=? AND path=?", (role, path)
        ).fetchone()
        if old is not None and old[0] != key:
            raise ManagedStorageCaptureUnavailable("capture_key_identity_conflict")
        spool.db.execute("INSERT OR IGNORE INTO names VALUES(?,?,?)", (role, path, key))
        return path

    def _reference(
        self,
        *,
        spool: _Spool,
        provider: ProviderManifest,
        role: str,
        key: str,
        native_class: str,
        native_id: str,
        recorded_bytes: str | None,
        required: bool,
    ) -> None:
        """Retain each actual logical source reference separately from its origin."""
        if native_class not in self._rules or self._rules[native_class][0] != role:
            raise ManagedStorageCaptureUnavailable("capture_native_class_unknown")
        path = self._name(spool=spool, provider=provider, role=role, key=key)
        spool.db.execute(
            "INSERT INTO refs(role,path,class,native_id,bytes,required) VALUES(?,?,?,?,?,?)",
            (role, path, native_class, native_id, recorded_bytes, int(required)),
        )

    def _stage_seed(
        self, *, spool: _Spool, seed: NativeStoreReference, provider: ProviderManifest
    ) -> None:
        """Expand only known native authority using immutable stored manifests."""
        if seed.native_class not in self._rules:
            raise ManagedStorageCaptureUnavailable("capture_native_class_unknown")
        role, key, identifier = (
            seed.store_role,
            seed.locator.get_secret_value(),
            seed.native_id.get_secret_value(),
        )
        if role == "knowledge":
            raise ManagedStorageCaptureUnavailable(
                "capture_managed_k_profile_unsupported"
            )
        if seed.locator_kind == "journal_intent":
            key = "forget-manifests/" + key + ".json"
        self._reference(
            spool=spool,
            provider=provider,
            role=role,
            key=key,
            native_class=seed.native_class,
            native_id=identifier,
            recorded_bytes=seed.recorded_bytes,
            required=seed.native_state != "preparing",
        )
        if seed.native_class == "document_representations.conversion_uri":
            expected = (
                None
                if seed.recorded_digest is None
                else seed.recorded_digest.get_secret_value()
            )
            if expected is None:
                raise ManagedStorageCaptureUnavailable(
                    "capture_conversion_manifest_digest_missing"
                )
            payload = json.loads(
                self._read_manifest(
                    spool=spool,
                    provider=provider,
                    role=role,
                    key=key,
                    expected_digest=expected,
                )
            )
            base = key.rsplit("/", 1)[0]
            source_map = payload.get("source_map")
            if source_map is not None:
                if source_map.get("uri") != base + "/source_map.json" or not _is_digest(
                    value=source_map.get("sha256")
                ):
                    raise ManagedStorageCaptureUnavailable(
                        "capture_conversion_source_map"
                    )
                self._reference(
                    spool=spool,
                    provider=provider,
                    role=role,
                    key=source_map["uri"],
                    native_class="document_representations.source_map",
                    native_id=identifier,
                    recorded_bytes=None,
                    required=True,
                )
            for asset in payload["derived_assets"]:
                if asset.get("uri") != base + "/media/" + asset[
                    "name"
                ] or not _is_digest(value=asset.get("sha256")):
                    raise ManagedStorageCaptureUnavailable("capture_conversion_asset")
                self._reference(
                    spool=spool,
                    provider=provider,
                    role=role,
                    key=asset["uri"],
                    native_class="document_representations.derived_asset",
                    native_id=identifier + ":" + asset["name"],
                    recorded_bytes=None,
                    required=True,
                )

    def _stage_corpus_document(
        self, *, spool: _Spool, seed: NativeCorpusDocument, provider: ProviderManifest
    ) -> None:
        """Bind completed copies to recorded paths AND current publication inputs.

        Cleanup paths alone never imply publication. An old folder, a cleared
        or stale marker, or a deleted/unselected document leaves only staging.
        Read the hash-bound immutable E0 inventory, never mutable document.json.
        """
        document = seed.document
        published = (
            document is not None
            and document.doc_id == seed.doc_id
            and seed.paths == (document.path,)
            and document.corpus_published_revision == desired_export_revision(document)
        )
        for path in seed.paths:
            if not _CORPUS_FOLDER.fullmatch(path) or not path.endswith(
                "--" + corpus_suffix(seed.doc_id)
            ):
                raise ManagedStorageCaptureUnavailable("capture_corpus_path_invalid")
        if not published or document is None:
            return
        payload = json.loads(
            self._read_manifest(
                spool=spool,
                provider=provider,
                role="artifacts",
                key=document.conversion_uri,
                expected_digest=document.manifest_hash,
            )
        )
        files = {
            original_name(document.mime): "documents.corpus_original",
            "document.md": "documents.corpus_derivative",
            "pageindex.json": "documents.corpus_derivative",
            "document.json": "documents.corpus_derivative",
            "_index.md": "corpus.navigation",
        }
        if payload.get("source_map") is not None:
            files["source_map.json"] = "documents.corpus_derivative"
        for asset in payload["derived_assets"]:
            name = _ASSET_NAME.validate_python(asset["name"])
            if asset["kind"] in PIPELINE_ASSET_KINDS:
                continue
            if asset["kind"] not in READER_ASSET_KINDS:
                raise ManagedStorageCaptureUnavailable(
                    "capture_corpus_asset_kind_unknown"
                )
            key = "media/" + name
            if key in files:
                raise ManagedStorageCaptureUnavailable("capture_corpus_asset_duplicate")
            files[key] = "documents.corpus_derivative"
        for name, native_class in files.items():
            self._reference(
                spool=spool,
                provider=provider,
                role="corpus",
                key=document.path + "/" + name,
                native_class=native_class,
                native_id=f"{seed.doc_id}:{document.corpus_published_revision}:{name}",
                recorded_bytes=None,
                required=True,
            )

    def _read_manifest(
        self,
        *,
        spool: _Spool,
        provider: ProviderManifest,
        role: str,
        key: str,
        expected_digest: str | None,
    ) -> bytes:
        """Read a native-referenced immutable body already proved present at T."""
        path = self._name(spool=spool, provider=provider, role=role, key=key)
        row = spool.db.execute(
            "SELECT body FROM items WHERE role=? AND path=?", (role, path)
        ).fetchone()
        if row is None:
            raise ManagedStorageCaptureUnavailable(
                "capture_referenced_manifest_missing"
            )
        item = ProviderItem.model_validate_json(row[0])
        try:
            body = self._stores[role].read_bytes(key=ObjectKey(key))
        except Exception:
            raise ManagedStorageCaptureUnavailable(
                "capture_manifest_read_unavailable"
            ) from None
        if (
            item.entry_kind != "regular"
            or int(item.logical_bytes) != len(body)
            or (
                expected_digest is not None
                and hashlib.sha256(body).hexdigest() != expected_digest
            )
        ):
            raise ManagedStorageCaptureUnavailable("capture_manifest_version_mismatch")
        return body

    def _stage_native_allocations(
        self, *, spool: _Spool, native: NativeCapturedStorageView, capture_id: str
    ) -> None:
        """Use only the in-guard records and the accepted exact whole-root map."""
        for allocation in native.allocation_sample.relations:
            native_class = "postgres.public." + allocation.root_relation
            if native_class not in self._rules:
                raise ManagedStorageCaptureUnavailable(
                    "capture_native_root_unclassified"
                )
            _, purpose, component = self._rules[native_class]
            evidence = _digest(
                domain="rememberstack.storage_native_allocation.v1", record=allocation
            )
            self._observation(
                spool=spool,
                capture_id=capture_id,
                store="postgres",
                identity=allocation.relation_oid,
                purpose=purpose,
                evidence=evidence,
            )
            for fork in ("main", "fsm", "vm", "init"):
                identity = _opaque(
                    domain="rememberstack.native_fork.v1",
                    fields=(capture_id, allocation.relation_oid, fork),
                )
                spool.resource(
                    component=component,
                    store="postgres",
                    identity=identity,
                    version=evidence,
                    evidence=evidence,
                    measured_bytes=getattr(allocation, fork + "_bytes"),
                )

    def _classify_items(
        self,
        *,
        spool: _Spool,
        provider: ProviderManifest,
        capture_id: str,
        native: NativeCapturedStorageView,
    ) -> None:
        """Match both complete sets; a live listing supplies only private names."""
        unmatched = spool.db.execute(
            "SELECT 1 FROM items i LEFT JOIN names n USING(role,path) WHERE n.path IS NULL AND json_extract(i.body,'$.entry_kind')='regular' LIMIT 1"
        ).fetchone()
        if unmatched is not None:
            for role, store in self._stores.items():
                try:
                    for index, key in enumerate(store.iter_capture_key_candidates()):
                        self._name(spool=spool, provider=provider, role=role, key=key)
                        if index % 1024 == 0:
                            native.check_continuity()
                except Exception:
                    raise ManagedStorageCaptureUnavailable(
                        "capture_candidate_names_unavailable"
                    ) from None
        for role, path, body in spool.db.execute(
            "SELECT role,path,body FROM items ORDER BY role,path"
        ):
            item = ProviderItem.model_validate_json(body)
            evidence = _digest(
                domain="rememberstack.storage_provider_item.v1", record=item
            )
            if item.entry_kind == "directory":
                if item.logical_bytes != "0":
                    raise ManagedStorageCaptureUnavailable(
                        "capture_directory_representation"
                    )
                self._observation(
                    spool=spool,
                    capture_id=capture_id,
                    store=role,
                    identity=path,
                    purpose="adapter_metadata",
                    evidence=evidence,
                )
                continue
            refs = spool.db.execute(
                "SELECT class,native_id,bytes FROM refs WHERE role=? AND path=? ORDER BY class,native_id",
                (role, path),
            ).fetchall()
            if refs:
                ownership = {self._rules[row[0]][1:] for row in refs}
                if len(ownership) != 1:
                    raise ManagedStorageCaptureUnavailable(
                        "capture_mixed_origin_purpose"
                    )
                purpose, component = next(iter(ownership))
                for native_class, identifier, recorded_bytes in refs:
                    if (
                        recorded_bytes is not None
                        and recorded_bytes != item.logical_bytes
                    ):
                        raise ManagedStorageCaptureUnavailable(
                            "capture_reference_length_mismatch"
                        )
                    member = StorageReferenceMember(
                        opaque_id=self._native_opaque(
                            native_class=native_class, native_id=identifier
                        ),
                        payload_digest="0" * 64,
                        component="p3_origin"
                        if role == "corpus"
                        else "canonical_object",
                        purpose=purpose,
                        store_binding_id=role,
                        resource_identity=path,
                        version_identity=item.version_id,
                        logical_bytes=item.logical_bytes,
                        alias_group=item.alias_group,
                    )
                    spool.member(
                        member=member.model_copy(
                            update={
                                "payload_digest": member_payload_digest(member=member)
                            }
                        )
                    )
                spool.db.execute(
                    "UPDATE refs SET matched=1 WHERE role=? AND path=?", (role, path)
                )
            else:
                row = spool.db.execute(
                    "SELECT name FROM names WHERE role=? AND path=?", (role, path)
                ).fetchone()
                if row is None:
                    raise ManagedStorageCaptureUnavailable(
                        "capture_original_item_unmatched"
                    )
                native_class = self._unreferenced_class(role=role, key=row[0])
                _, purpose, component = self._rules[native_class]
            self._observation(
                spool=spool,
                capture_id=capture_id,
                store=role,
                identity=path,
                purpose=purpose,
                evidence=evidence,
            )
            spool.resource(
                component=component,
                store=role,
                identity=path,
                version=item.version_id,
                evidence=evidence,
                measured_bytes=item.logical_bytes,
            )
        if (
            spool.db.execute(
                "SELECT 1 FROM refs WHERE required=1 AND matched=0 LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise ManagedStorageCaptureUnavailable("capture_native_reference_unmatched")

    def _unreferenced_class(self, *, role: str, key: str) -> str:
        """Classify only explicit current writer grammars, never an unknown file."""
        if role == "raw" and _UPLOAD.fullmatch(key):
            return "raw.unregistered_upload"
        if role == "raw" and _RAW_ATTEMPT.fullmatch(key):
            return "raw.unregistered_original"
        if role == "artifacts" and _CHECKPOINT.fullmatch(key):
            return "artifacts.conversion_checkpoint"
        if role == "artifacts" and _REP_ATTEMPT.fullmatch(key):
            return "artifacts.unregistered_representation"
        if role == "corpus":
            # Match a document folder before shared navigation: its _index.md
            # is staging too until that document's publication is complete.
            if _CORPUS_KEY.fullmatch(key):
                return "corpus.incomplete_or_orphan"
            if key == "llms.txt" or _CORPUS_NAVIGATION.fullmatch(key):
                return "corpus.navigation"
        raise ManagedStorageCaptureUnavailable("capture_writer_class_unknown")

    def _native_opaque(self, *, native_class: str, native_id: str) -> str:
        """Hide private native hashes/coordinates under deployment-local custody."""
        message = (
            "rememberstack.storage_native_reference.v1\0"
            + self._profile.bindings.deployment_id
            + "\0"
            + native_class
            + "\0"
            + native_id
        ).encode()
        return hmac.new(
            self._identity_key.get_secret_value().encode(), message, hashlib.sha256
        ).hexdigest()

    def _observation(
        self,
        *,
        spool: _Spool,
        capture_id: str,
        store: str,
        identity: str,
        purpose: StoragePurpose,
        evidence: str,
    ) -> None:
        """Retain actual included/excluded exposure without adding another charge."""
        member = StorageResourceObservation(
            opaque_id=_opaque(
                domain="rememberstack.storage_observation_identity.v1",
                fields=(capture_id, store, identity),
            ),
            payload_digest="0" * 64,
            store_binding_id=store,
            purpose=purpose,
            resource_evidence_digest=evidence,
        )
        spool.member(
            member=member.model_copy(
                update={"payload_digest": member_payload_digest(member=member)}
            )
        )

    def _publish(
        self,
        *,
        spool: _Spool,
        request: StorageCaptureRequest,
        witness: StorageNativeWitness,
        provider: ProviderManifest,
    ) -> StorageCaptureManifest:
        """Write exact pages and all four component records, then completion last."""
        capture_id = capture_request_id(request=request)
        native_digest = native_witness_digest(witness=witness)
        components: list[StorageComponentRecord] = []
        for component in _COMPONENTS:
            resource_digest, measured = _resource_set(
                spool=spool, capture_id=capture_id, component=component
            )
            evidence = _ComponentEvidence(
                contract="rememberstack.storage_component_evidence.v1",
                capture_id=capture_id,
                component=component,
                native_witness_digest=native_digest,
                provider_manifest_digest=provider.manifest_digest,
                classification_digest=request.classification_digest,
                operator_mapping_digest=request.operator_mapping_digest,
                resource_set_digest=resource_digest,
                measured_bytes=str(measured),
            )
            components.append(
                StorageComponentRecord(
                    component=component,
                    measured_bytes=str(measured),
                    classification_digest=request.classification_digest,
                    operator_mapping_digest=request.operator_mapping_digest,
                    resource_set_digest=resource_digest,
                    evidence_digest=_digest(domain=evidence.contract, record=evidence),
                )
            )
        digests: list[str] = []
        counts = dict(
            spool.db.execute("SELECT kind,count(*) FROM members GROUP BY kind")
        )
        for page in iter_capture_pages(
            request=request,
            members=spool.members(),
            page_member_limit=STORAGE_CAPTURE_PAGE_MEMBER_LIMIT,
        ):
            self._evidence.persist_page(
                capture_id=capture_id,
                page_ordinal=page.page_ordinal,
                body=profile_bytes(record=page),
            )
            digests.append(page.page_digest)
        manifest = StorageCaptureManifest(
            capture_id=capture_id,
            request=request,
            native_witness_digest=native_digest,
            provider_manifest_digest=provider.manifest_digest,
            component_records=tuple(components),
            reference_count=str(counts.get("reference", 0)),
            terminal_count=str(counts.get("processing_outcome", 0)),
            resource_observation_count=str(counts.get("resource_observation", 0)),
            page_count=str(len(digests)),
            page_digests=tuple(digests),
            set_digest=membership_set_digest(page_digests=digests),
            manifest_digest="0" * 64,
        )
        manifest = manifest.model_copy(
            update={"manifest_digest": manifest_payload_digest(manifest=manifest)}
        )
        _check_durable_pages(evidence_store=self._evidence, manifest=manifest)
        self._evidence.complete(
            capture_id=capture_id, manifest=profile_bytes(record=manifest)
        )
        return manifest


def check_capture_configuration(
    *,
    request: StorageCaptureRequest,
    profile: ManagedStorageCaptureProfile,
    stores: Mapping[str, StorageCaptureObjectStore],
    identity_key: SecretStr,
) -> None:
    """Require exact current rules/documents and actual distinct store bindings."""
    expected = build_managed_storage_profile(
        native=profile.native,
        bindings=profile.bindings,
        managed_runtime_source_digest=profile.managed_runtime_source_digest,
        provider_profile_digest=profile.provider_profile_digest,
        provider_clock_profile_digest=profile.provider_clock_profile_digest,
    )
    if profile != expected or request != profile.request(
        cut_at_unix_us=request.cut_at_unix_us
    ):
        raise ManagedStorageCaptureUnavailable("capture_profile_document_mismatch")
    if set(stores) != {"raw", "artifacts", "corpus", "forget"}:
        raise ManagedStorageCaptureUnavailable("capture_store_scope")
    for role, store in stores.items():
        if store.capture_store_identity() != profile.bindings.filesystem_for(role=role):
            raise ManagedStorageCaptureUnavailable("capture_store_binding_mismatch")
    if not identity_key.get_secret_value():
        raise ManagedStorageCaptureUnavailable("capture_identity_key_missing")


def _check_durable_pages(
    *, evidence_store: StorageCaptureEvidenceStore, manifest: StorageCaptureManifest
) -> None:
    """Refuse missing/changed persisted membership before completing/replaying."""

    def pages() -> Iterator[StorageCapturePage]:
        """Read one exact canonical page, with no whole-set RAM retention."""
        for ordinal in range(int(manifest.page_count)):
            body = evidence_store.read_page(
                capture_id=manifest.capture_id, page_ordinal=str(ordinal)
            )
            if body is None:
                raise ManagedStorageCaptureUnavailable("capture_durable_page_missing")
            page = StorageCapturePage.model_validate_json(body)
            if body != profile_bytes(record=page):
                raise ManagedStorageCaptureUnavailable(
                    "capture_durable_page_not_canonical"
                )
            yield page

    check_capture_encoding(
        manifest=manifest,
        pages=pages(),
        page_member_limit=STORAGE_CAPTURE_PAGE_MEMBER_LIMIT,
    )


def replay_completed_capture(
    *,
    evidence_store: StorageCaptureEvidenceStore,
    request: StorageCaptureRequest,
    body: bytes,
) -> CompletedStorageCapture:
    """Replay only durably completed identical bytes; never reopen native state."""
    manifest = StorageCaptureManifest.model_validate_json(body)
    if manifest.request != request or body != profile_bytes(record=manifest):
        raise ManagedStorageCaptureUnavailable("capture_completed_identity_conflict")
    native_body = evidence_store.read_native_witness(capture_id=manifest.capture_id)
    provider_body = evidence_store.read_provider_manifest(
        capture_id=manifest.capture_id
    )
    if native_body is None or provider_body is None:
        raise ManagedStorageCaptureUnavailable("capture_completed_evidence_missing")
    witness = StorageNativeWitness.model_validate_json(native_body)
    provider = ProviderManifest.model_validate_json(provider_body)
    if (
        native_witness_digest(witness=witness) != manifest.native_witness_digest
        or provider.manifest_digest != manifest.provider_manifest_digest
        or witness.capture_id != manifest.capture_id
        or witness.deployment_id != request.deployment_id
        or witness.profile_digest != request.native_capture_profile_digest
        or provider.request.cut_at_unix_us != request.cut_at_unix_us
        or native_body != profile_bytes(record=witness)
        or provider_body != provider_canonical_bytes(record=provider)
    ):
        raise ManagedStorageCaptureUnavailable("capture_completed_evidence_conflict")
    check_provider_manifest(manifest=provider)
    _check_durable_pages(evidence_store=evidence_store, manifest=manifest)
    return CompletedStorageCapture(
        manifest=manifest,
        native_witness=witness,
        provider_manifest=provider,
        _store=evidence_store,
    )


class _ComponentEvidence(BaseModel):
    """The exact D152 component-evidence preimage, without its own digest."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    contract: Literal["rememberstack.storage_component_evidence.v1"]
    capture_id: str
    component: StorageComponent
    native_witness_digest: str
    provider_manifest_digest: str
    classification_digest: str
    operator_mapping_digest: str
    resource_set_digest: str
    measured_bytes: str


def _digest(*, domain: str, record: BaseModel) -> str:
    """Bind one closed content-free native/provider record under its profile."""
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + profile_bytes(record=record)
    ).hexdigest()


def _opaque(*, domain: str, fields: tuple[str, ...]) -> str:
    """Name only actual public capture/resource metadata using length framing."""
    digest = hashlib.sha256(domain.encode("ascii") + b"\0")
    for value in fields:
        encoded = value.encode("ascii")
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
    return digest.hexdigest()


def _is_digest(*, value: object) -> bool:
    """Refuse malformed private manifest identities without exposing their values."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _resource_set(
    *, spool: _Spool, capture_id: str, component: StorageComponent
) -> tuple[str, int]:
    """Stream the exact sorted resource document and checked component sum."""
    prefix = (
        '{"capture_id":'
        + json.dumps(capture_id)
        + ',"component":'
        + json.dumps(component)
        + ',"contract":"rememberstack.storage_resource_set.v1","resources":['
    )
    digest = hashlib.sha256(
        b"rememberstack.storage_resource_set.v1\0" + prefix.encode()
    )
    first, measured = True, 0
    for store, identity, version, evidence, amount in spool.db.execute(
        "SELECT store,id,version,evidence,bytes FROM resources WHERE component=? ORDER BY store,id,version",
        (component,),
    ):
        if not first:
            digest.update(b",")
        record = {
            "store_binding_id": store,
            "resource_identity": identity,
            "version_identity": version,
            "resource_evidence_digest": evidence,
            "measured_bytes": amount,
        }
        digest.update(
            json.dumps(
                record, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        measured += int(amount)
        first = False
    digest.update(b"]}")
    return digest.hexdigest(), measured
