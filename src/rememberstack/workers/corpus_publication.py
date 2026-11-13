"""D155 idempotent publication and verifying reconcile over the same live tree.

This capability is deliberately unbound from serving profiles until lifecycle
removal, restore and readiness switch together. It performs no paid model work.
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
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import DEFAULT_CORPUS_POLICY
from rememberstack.core.corpus_export import GeneratedFile
from rememberstack.core.corpus_export import render_navigation
from rememberstack.model import ClaimedWork
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.object_store import MutableObjectStorePort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.ports.object_store import StreamingReadPort
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
            folders, navigation = self._discover(lock=held)
            revision = self._publish_document(
                lock=held,
                doc_id=doc_id,
                folders=folders.get(corpus_suffix(doc_id), set()),
                verify=verify,
            )
            self._navigation(lock=held, existing=navigation)
            return self._catalog.finish(
                lock=held, doc_id=doc_id, revision=revision, policy=self.policy
            )

    def reconcile(self, *, deployment_id: UUID, verify: bool = True) -> int:
        """Share one discovery pass; verify digests even behind matching markers."""
        with self._catalog.locked(deployment_id=deployment_id) as lock:
            folders, navigation = self._discover(lock=lock, remove_loose=True)
            self._store.abort_uploads(prefix="", before_write=lock.check)
            published: list[tuple[UUID, str | None]] = []
            # Keep only one document inventory at a time. Discovery stores
            # folder paths, not asset keys, and is shared across this run.
            with self._readers.documents(deployment_id=deployment_id) as documents:
                for document in documents:
                    revision = self._publish_document(
                        lock=lock,
                        doc_id=document.doc_id,
                        folders=folders.pop(corpus_suffix(document.doc_id), set()),
                        verify=verify,
                    )
                    published.append((document.doc_id, revision))
            for paths in folders.values():
                for path in paths:
                    self._remove_folder(lock=lock, path=path)
            self._navigation(lock=lock, existing=navigation)
            # Only small input identities survive the metadata pass; no stored
            # receipts or export inventories. Concurrent changes get fresh work.
            for doc_id, revision in published:
                self._catalog.finish(
                    lock=lock, doc_id=doc_id, revision=revision, policy=self.policy
                )
            return len(published)

    def _discover(
        self, *, lock: CorpusPublicationLock, remove_loose: bool = False
    ) -> tuple[dict[str, set[str]], set[str]]:
        """Enumerate navigation even after a crash removed the last old-folder key.

        Rebuilding all discovered navigation from DB membership covers stale
        doc-id/suffix rows without parsing their escaped display text or links.
        """
        folders: dict[str, set[str]] = defaultdict(set)
        navigation: set[str] = set()
        for key in self._store.iter_keys(prefix=""):
            if found := document_folder(key.root):
                path, suffix = found
                folders[suffix].add(path)
            elif posixpath.basename(key.root) == "_index.md" or key.root == "llms.txt":
                navigation.add(key.root)
            elif remove_loose:
                self._delete(lock=lock, key=key)
        return folders, navigation

    def _publish_document(
        self,
        *,
        lock: CorpusPublicationLock,
        doc_id: UUID,
        folders: set[str],
        verify: bool,
    ) -> str | None:
        document = self._catalog.clear(lock=lock, doc_id=doc_id)
        if document is None:
            for path in folders:
                self._remove_folder(lock=lock, path=path)
            return None
        export = load_document_export(
            document, artifact_store=self._artifacts, policy=self.policy
        )
        existing = (
            {key.root for key in self._store.iter_keys(prefix=export.path)}
            if export.path in folders
            else set()
        )
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
        return export.revision

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

    def _navigation(self, *, lock: CorpusPublicationLock, existing: set[str]) -> None:
        """Regenerate metadata only; no unrelated originals/media are ever touched."""
        with self._readers.documents(deployment_id=lock.deployment_id) as documents:
            desired = render_navigation(documents, policy=self.policy)
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
