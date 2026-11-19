"""D155 idempotent publication and verifying reconcile over the same live tree.

Publication, repair and D74 erasure share the same lock and renderer. None
performs paid model work.
"""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import nullcontext
import hashlib
import posixpath
import re
from typing import Protocol
from uuid import UUID

from rememberstack.core.corpus_export import CopyFile
from rememberstack.core.corpus_export import corpus_policy_version
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import CORPUS_TYPES
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import DEFAULT_CORPUS_POLICY
from rememberstack.core.corpus_export import GeneratedFile
from rememberstack.core.corpus_export import render_navigation
from rememberstack.model import ClaimedWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectPurgeEvidence
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.object_store import MutableObjectStorePort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.ports.object_store import StreamingReadPort
from rememberstack.ports.purge import ObjectPurgePort
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.corpus_publication import CorpusPublicationCatalog
from rememberstack.spine.corpus_publication import CorpusPublicationLock
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.corpus_export import load_document_export

_FOLDER = re.compile(r".+--([0-9a-f]{24})\Z")
_CHUNK_SIZE = 1024 * 1024


class CorpusSourceStore(ObjectStorePort, StreamingReadPort, Protocol):
    """Immutable inventory reads and bounded original/media streams."""


def document_folder(key: str) -> tuple[str, str] | None:
    """Find the first document component, never an impersonating asset below it."""
    parts = key.split("/")
    for index, part in enumerate(parts[:-1]):
        if match := _FOLDER.fullmatch(part):
            return "/".join(parts[: index + 1]), match[1]
    return None


class CorpusPublisher:
    """Stream desired files, remove stale copies and regenerate DB navigation."""

    def __init__(
        self,
        *,
        catalog: CorpusPublicationCatalog,
        readers: CorpusReaderCatalog,
        raw_store: CorpusSourceStore,
        artifact_store: CorpusSourceStore,
        corpus_store: MutableObjectStorePort,
        policy: CorpusPolicy = DEFAULT_CORPUS_POLICY,
    ) -> None:
        self._catalog = catalog
        self._readers = readers
        self._raw = raw_store
        self._artifacts = artifact_store
        self._store = corpus_store
        self.policy = policy

    def seed_current_policy(self, *, deployment_id: UUID) -> int:
        """Use ordinary events for a policy cut or obsolete-policy delivery."""
        return self._catalog.seed_current_policy(
            deployment_id=deployment_id, policy=self.policy
        )

    def publish_corpus(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        work: ClaimedWork | None = None,
        lock: CorpusPublicationLock | None = None,
        verify: bool = False,
    ) -> bool:
        """Re-read authority; return False only when newer inputs need another event.

        Internal callers can reuse a held lock. No event's former desired path,
        revision or processing order can override the current database selection.
        """
        with (
            nullcontext(lock)
            if lock
            else self._catalog.locked(deployment_id=deployment_id, work=work)
        ) as held:
            if held.deployment_id != deployment_id:
                raise ValueError("corpus lock belongs to another deployment")
            folders = self._catalog.paths(lock=held, doc_id=doc_id)
            revision, path = self._publish_document(
                lock=held, doc_id=doc_id, folders=folders, verify=verify
            )
            ancestors = {""}
            for folder in folders:
                parent = posixpath.dirname(folder)
                while parent:
                    ancestors.add(parent)
                    parent = posixpath.dirname(parent)
            self._navigation(lock=held, ancestors=ancestors)
            self._catalog.set_paths(
                lock=held, doc_id=doc_id, paths={path} if path else set()
            )
            return self._catalog.finish(
                lock=held, doc_id=doc_id, revision=revision, policy=self.policy
            )

    def reconcile(
        self,
        *,
        deployment_id: UUID,
        verify: bool = True,
        work: ClaimedWork | None = None,
    ) -> int:
        """Share one discovery pass; verify digests even behind matching markers."""
        with self._catalog.locked(deployment_id=deployment_id, work=work) as lock:
            folders, navigation = self._discover(lock=lock)
            self._store.abort_uploads(prefix="", before_write=lock.check)
            published: list[tuple[UUID, str | None, str | None]] = []
            # Keep only one document inventory at a time. Discovery stores
            # folder paths, not asset keys, and is shared across this run.
            with self._readers.documents(deployment_id=deployment_id) as documents:
                for document in documents:
                    paths = folders.pop(corpus_suffix(document.doc_id), set())
                    paths.update(self._catalog.paths(lock=lock, doc_id=document.doc_id))
                    revision, path = self._publish_document(
                        lock=lock, doc_id=document.doc_id, folders=paths, verify=verify
                    )
                    published.append((document.doc_id, revision, path))
            for paths in folders.values():
                for path in paths:
                    self._remove_folder(lock=lock, path=path)
            self._navigation(lock=lock, existing=navigation)
            # Only small input identities survive the metadata pass; no stored
            # receipts or export inventories. Concurrent changes get fresh work.
            self._catalog.clear_unselected_paths(
                lock=lock, doc_ids=[doc_id for doc_id, _, _ in published]
            )
            for doc_id, revision, path in published:
                self._catalog.set_paths(
                    lock=lock, doc_id=doc_id, paths={path} if path else set()
                )
                self._catalog.finish(
                    lock=lock, doc_id=doc_id, revision=revision, policy=self.policy
                )
            return len(published)

    def erase_document(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
        purger: ObjectPurgePort,
    ) -> ObjectPurgeEvidence | None:
        """D74: erase this suffix, then regenerate metadata from scrubbed authority.

        Discovery includes retained/delete-marked keys and uncompleted uploads.
        Even an empty recorded set MUST scan: a bucket can be restored separately
        from PostgreSQL. Survivor document folders are never written or deleted.
        """
        with self._catalog.locked(
            deployment_id=deployment_id, forget_id=forget_id
        ) as lock:
            if self._catalog.clear(lock=lock, doc_id=doc_id) is not None:
                raise RuntimeError(
                    "corpus erasure requires scrubbed document authority"
                )
            folders = self._catalog.paths(lock=lock, doc_id=doc_id)
            navigation: set[str] = set()
            suffix = corpus_suffix(doc_id)
            for key in purger.iter_purge_keys():
                if found := document_folder(key.root):
                    if found[1] == suffix:
                        folders.add(found[0])
                elif _is_navigation(key.root):
                    navigation.add(key.root)
            # Navigation can contain the source even after its last folder key
            # disappeared. Erase its retained versions too, then render it anew.
            keys = tuple(ObjectKey(path) for path in sorted(navigation))
            prefixes = tuple(ObjectKey(path) for path in sorted(folders))
            evidence = purger.purge_objects(
                keys=keys, prefixes=prefixes, before_write=lock.check
            )
            purger.verify_objects_purged(keys=keys, prefixes=prefixes)
            self._navigation(lock=lock, existing=set())
            self._verify_erasure(lock=lock, doc_id=doc_id, purger=purger)
            self._catalog.set_paths(lock=lock, doc_id=doc_id, paths=set())
            return evidence

    def verify_erased(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
        purger: ObjectPurgePort,
    ) -> None:
        with self._catalog.locked(
            deployment_id=deployment_id, forget_id=forget_id
        ) as lock:
            self._verify_erasure(lock=lock, doc_id=doc_id, purger=purger)
            if self._catalog.paths(lock=lock, doc_id=doc_id):
                raise RuntimeError("corpus erasure left recorded friendly paths")

    def _verify_erasure(
        self, *, lock: CorpusPublicationLock, doc_id: UUID, purger: ObjectPurgePort
    ) -> None:
        suffix = corpus_suffix(doc_id)
        navigation: set[str] = set()
        for key in purger.iter_purge_keys():
            if found := document_folder(key.root):
                if found[1] == suffix:
                    raise RuntimeError(f"corpus erasure verification found {key.root}")
            elif _is_navigation(key.root):
                navigation.add(key.root)
        with self._readers.documents(deployment_id=lock.deployment_id) as documents:
            desired = render_navigation(documents, policy=self.policy)
        if navigation != desired.keys() or any(
            self._store.sha256_of(key=ObjectKey(path))
            != hashlib.sha256(content).hexdigest()
            for path, content in desired.items()
        ):
            raise RuntimeError("corpus erasure verification found stale navigation")

    def _discover(
        self, *, lock: CorpusPublicationLock
    ) -> tuple[dict[str, set[str]], set[str]]:
        """Full-store repair only: recover paths/navigation missed by DB state."""
        folders: dict[str, set[str]] = defaultdict(set)
        navigation: set[str] = set()
        for key in self._store.iter_keys(prefix=""):
            if found := document_folder(key.root):
                path, suffix = found
                folders[suffix].add(path)
            elif _is_navigation(key.root):
                navigation.add(key.root)
            else:
                self._delete(lock=lock, key=key)
        return folders, navigation

    def _publish_document(
        self,
        *,
        lock: CorpusPublicationLock,
        doc_id: UUID,
        folders: set[str],
        verify: bool,
    ) -> tuple[str | None, str | None]:
        document = self._catalog.clear(lock=lock, doc_id=doc_id)
        if document is None:
            for path in folders:
                self._remove_folder(lock=lock, path=path)
            return None, None
        folders.add(document.path)
        self._catalog.set_paths(lock=lock, doc_id=doc_id, paths=folders)
        export = load_document_export(
            document, artifact_store=self._artifacts, policy=self.policy
        )
        existing = {key.root for key in self._store.iter_keys(prefix=export.path)}
        desired = {export.path + "/" + file.path for file in export.files}
        unchanged = not verify and document.corpus_published_revision == export.revision
        changed = [
            file
            for file in export.files
            if not (unchanged and export.path + "/" + file.path in existing)
            and self._store.sha256_of(key=ObjectKey(export.path + "/" + file.path))
            != file.sha256
        ]
        index = next(file for file in export.files if file.path == "_index.md")
        assert isinstance(index, GeneratedFile)
        if changed or existing - desired:
            self._store.abort_uploads(prefix=export.path, before_write=lock.check)
            if existing:
                updating = index.content.replace(b"State: ready", b"State: updating", 1)
                self._put(
                    lock=lock,
                    path=export.path + "/_index.md",
                    file=GeneratedFile("_index.md", updating),
                )
            for file in changed:
                if isinstance(file, CopyFile):
                    self._put(lock=lock, path=export.path + "/" + file.path, file=file)
            for key in sorted(existing - desired):
                self._delete(lock=lock, key=ObjectKey(key))
            for file in changed:
                if isinstance(file, GeneratedFile) and file.path not in {
                    "document.json",
                    "_index.md",
                }:
                    self._put(lock=lock, path=export.path + "/" + file.path, file=file)
            metadata = next(
                file for file in export.files if file.path == "document.json"
            )
            self._put(lock=lock, path=export.path + "/document.json", file=metadata)
            self._put(lock=lock, path=export.path + "/_index.md", file=index)
        for old in folders - {export.path}:
            self._remove_folder(lock=lock, path=old)
        return export.revision, export.path

    def _put(
        self, *, lock: CorpusPublicationLock, path: str, file: CopyFile | GeneratedFile
    ) -> None:
        chunks = (
            self._stream(file=file)
            if isinstance(file, CopyFile)
            else iter((file.content,))
        )
        self._store.replace_stream(
            key=ObjectKey(path),
            chunks=chunks,
            storage_class="hot",
            sha256=file.sha256,
            before_write=lock.check,
        )

    def _stream(self, *, file: CopyFile) -> Iterator[bytes]:
        """Validate source integrity at EOF, before the adapter completes a PUT."""
        store = self._raw if file.store == "raw" else self._artifacts
        source = store.iter_bytes(key=ObjectKey(file.uri), chunk_size=_CHUNK_SIZE)
        digest = hashlib.sha256()
        try:
            for chunk in source:
                digest.update(chunk)
                yield chunk
            if digest.hexdigest() != file.sha256:
                raise ValueError(f"immutable corpus input hash mismatch: {file.uri}")
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()

    def _delete(self, *, lock: CorpusPublicationLock, key: ObjectKey) -> None:
        lock.check()
        self._store.delete(key=key)

    def _remove_folder(self, *, lock: CorpusPublicationLock, path: str) -> None:
        self._store.abort_uploads(prefix=path, before_write=lock.check)
        for key in self._store.iter_keys(prefix=path):
            self._delete(lock=lock, key=key)

    def _navigation(
        self,
        *,
        lock: CorpusPublicationLock,
        existing: set[str] | None = None,
        ancestors: set[str] | None = None,
    ) -> None:
        """List only affected radix subtrees; full discovery belongs to reconcile."""
        if ancestors is not None:
            # Root links include all fixed types, even empty ones. Bootstrap
            # that constant-size scaffold once, without inspecting other topics.
            if self._store.sha256_of(key=ObjectKey("llms.txt")) is None:
                ancestors.update(CORPUS_TYPES)
            existing = {posixpath.join(parent, "_index.md") for parent in ancestors}
            for parent in ancestors:
                existing.update(
                    key.root
                    for key in self._store.iter_keys(prefix=posixpath.join(parent, "~"))
                )
        assert existing is not None
        with self._readers.documents(deployment_id=lock.deployment_id) as documents:
            desired = render_navigation(
                documents, policy=self.policy, ancestors=ancestors
            )
        for path in sorted(desired, key=lambda name: (-name.count("/"), name)):
            file = GeneratedFile(path, desired[path])
            if self._store.sha256_of(key=ObjectKey(path)) != file.sha256:
                self._put(lock=lock, path=path, file=file)
        for path in sorted(existing - desired.keys()):
            self._delete(lock=lock, key=ObjectKey(path))


class PublishCorpusHandler:
    """One ordinary event, executed by the existing class or pushed task runner."""

    def __init__(self, *, publisher: CorpusPublisher) -> None:
        self._publisher = publisher

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        payload = work.payload or {}
        try:
            if set(payload) != {"doc_id"}:
                raise ValueError("publication payload must contain only doc_id")
            doc_id = UUID(str(payload["doc_id"]))
        except (ValueError, KeyError) as error:
            raise NonRetryableHandlerError(
                "invalid corpus publication event"
            ) from error
        if work.component_version != corpus_policy_version(self._publisher.policy):
            # Stale policy delivery must not write; seed the current policy through
            # ordinary work instead of changing this event or resetting attempts.
            self._publisher.seed_current_policy(deployment_id=work.deployment_id)
            return HandlerOutcome()
        self._publisher.publish_corpus(
            deployment_id=work.deployment_id, doc_id=doc_id, work=work
        )
        return HandlerOutcome()


class ReconcileCorpusHandler:
    """Digest verification delivered by the ordinary unlaned delayed-work route."""

    def __init__(
        self,
        *,
        publisher: CorpusPublisher,
        catalog: CorpusPublicationCatalog,
        interval_s: float,
    ) -> None:
        self._publisher = publisher
        self._catalog = catalog
        self._interval_s = interval_s

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        if work.component_version == corpus_policy_version(self._publisher.policy):
            self._publisher.reconcile(
                deployment_id=work.deployment_id, verify=True, work=work
            )
        self._catalog.schedule_verification(
            deployment_id=work.deployment_id,
            interval_s=self._interval_s,
            policy=self._publisher.policy,
        )
        return HandlerOutcome()


def _is_navigation(path: str) -> bool:
    return posixpath.basename(path) == "_index.md" or path == "llms.txt"
