"""D155 idempotent publication and verifying reconcile over the same live tree.

Publication and repair share one lock and renderer; D159 forget erasure uses
the same renderer without the lock. None performs paid model work.
"""

from collections import defaultdict
from collections.abc import Callable
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
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.object_store import MutableObjectStorePort
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.ports.object_store import StreamingReadPort
from rememberstack.ports.purge import ObjectPurgePort
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.corpus_export import served_corpus_document_ids_on
from rememberstack.spine.corpus_publication import CorpusPublicationCatalog
from rememberstack.spine.corpus_publication import CorpusPublicationLock
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.corpus_export import load_document_export

_FOLDER = re.compile(r".+--([0-9a-f]{24})\Z")
_CHUNK_SIZE = 1024 * 1024


class CorpusSourceStore(ObjectStorePort, StreamingReadPort, Protocol):
    """Immutable inventory reads and bounded original/media streams."""


class CorpusStore(MutableObjectStorePort, ObjectStorePort, Protocol):
    """The live corpus bucket: complete-object writes and whole-object reads."""

    def remove_empty_folder_markers(
        self, *, prefix: str, before_write: Callable[[], None] | None = None
    ) -> None:
        """Delete folders a gateway keeps (and lists as "x/") once emptied."""
        ...


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
        corpus_store: CorpusStore,
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
            self._navigation(
                deployment_id=held.deployment_id, lock=held, ancestors=ancestors
            )
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
            with lock.transaction() as connection:
                doc_ids = served_corpus_document_ids_on(
                    connection=connection, deployment_id=deployment_id
                )
            for doc_id in doc_ids:
                paths = folders.pop(corpus_suffix(doc_id), set())
                paths.update(self._catalog.paths(lock=lock, doc_id=doc_id))
                revision, path = self._publish_document(
                    lock=lock, doc_id=doc_id, folders=paths, verify=verify
                )
                published.append((doc_id, revision, path))
            for paths in folders.values():
                for path in paths:
                    self._remove_folder(lock=lock, path=path)
            self._navigation(
                deployment_id=lock.deployment_id, lock=lock, existing=navigation
            )
            # Only small input identities survive the metadata pass; no stored
            # receipts or export inventories. Concurrent changes get fresh work.
            self._catalog.clear_unselected_paths(lock=lock, doc_ids=doc_ids)
            for doc_id, revision, path in published:
                self._catalog.set_paths(
                    lock=lock, doc_id=doc_id, paths={path} if path else set()
                )
                self._catalog.finish(
                    lock=lock, doc_id=doc_id, revision=revision, policy=self.policy
                )
            return len(published)

    def erase_document(
        self, *, deployment_id: UUID, doc_id: UUID, purger: ObjectPurgePort
    ) -> tuple[bool, tuple[str, ...]]:
        """D159 forget: erase this suffix and regenerate the navigation that listed it.

        Takes no publication lock: it deletes only the document's own folders
        (every object version, found by suffix) and rewrites only navigation
        above them or listing the suffix, from the database, which no longer
        serves the document. Navigation is rewritten before anything is
        deleted, so an interrupted step never leaves a survivor's listing
        missing; retained versions that still list the suffix go last. A
        publisher racing it can at worst write navigation that lists the
        document again; the forget's next sweep finds and removes that.
        Returns whether anything was removed, and the removed folders and
        rewritten navigation paths (for mount invalidation).
        """
        suffix = corpus_suffix(doc_id)
        folders: set[str] = set()
        navigation: set[str] = set()
        for key in purger.iter_purge_keys():
            if found := document_folder(key.root):
                if found[1] == suffix:
                    folders.add(found[0])
            elif _is_navigation(key.root):
                navigation.add(key.root)
        listing = {path for path in navigation if self._lists(path=path, suffix=suffix)}
        ancestors = {""}
        for path in folders | {posixpath.dirname(path) for path in listing}:
            parent = posixpath.dirname(path) if path in folders else path
            while parent:
                ancestors.add(parent)
                parent = posixpath.dirname(parent)
        if folders or listing:
            self._navigation(deployment_id=deployment_id, ancestors=ancestors)

        removed = 0
        if folders:
            removed += purger.purge_objects(
                keys=(), prefixes=tuple(ObjectKey(path) for path in sorted(folders))
            )
        # The navigation rewrite dropped the index of a topic that only this
        # document filled; a gateway that keeps folders still lists the topic.
        for parent in sorted(ancestors - {""}, key=lambda name: -name.count("/")):
            self._store.remove_empty_folder_markers(prefix=parent)
        removed += purger.purge_retained_versions_containing(
            keys=tuple(ObjectKey(path) for path in sorted(navigation)),
            needle=suffix.encode(),
        )
        for path in navigation:
            if self._lists(path=path, suffix=suffix):
                raise RuntimeError(
                    f"corpus navigation still lists the document: {path}"
                )
        rewritten = {posixpath.join(parent, "_index.md") for parent in ancestors}
        rewritten |= {"llms.txt"} | listing
        return bool(folders or listing or removed), tuple(sorted(folders | rewritten))

    def _lists(self, *, path: str, suffix: str) -> bool:
        """Whether one current navigation file names a document suffix."""
        try:
            return suffix.encode() in self._store.read_bytes(key=ObjectKey(path))
        except FileNotFoundError:
            return False

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
                self._delete(before_write=lock.check, key=key)
        self._store.remove_empty_folder_markers(prefix="", before_write=lock.check)
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
                    before_write=lock.check,
                    path=export.path + "/_index.md",
                    file=GeneratedFile("_index.md", updating),
                )
            for file in changed:
                if isinstance(file, CopyFile):
                    self._put(
                        before_write=lock.check,
                        path=export.path + "/" + file.path,
                        file=file,
                    )
            for key in sorted(existing - desired):
                self._delete(before_write=lock.check, key=ObjectKey(key))
            if existing - desired:
                self._store.remove_empty_folder_markers(
                    prefix=export.path, before_write=lock.check
                )
            for file in changed:
                if isinstance(file, GeneratedFile) and file.path not in {
                    "document.json",
                    "_index.md",
                }:
                    self._put(
                        before_write=lock.check,
                        path=export.path + "/" + file.path,
                        file=file,
                    )
            metadata = next(
                file for file in export.files if file.path == "document.json"
            )
            self._put(
                before_write=lock.check,
                path=export.path + "/document.json",
                file=metadata,
            )
            self._put(
                before_write=lock.check, path=export.path + "/_index.md", file=index
            )
        for old in folders - {export.path}:
            self._remove_folder(lock=lock, path=old)
        return export.revision, export.path

    def _put(
        self,
        *,
        path: str,
        file: CopyFile | GeneratedFile,
        before_write: Callable[[], None] | None,
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
            before_write=before_write,
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

    def _delete(
        self, *, key: ObjectKey, before_write: Callable[[], None] | None
    ) -> None:
        if before_write is not None:
            before_write()
        self._store.delete(key=key)

    def _remove_folder(self, *, lock: CorpusPublicationLock, path: str) -> None:
        self._store.abort_uploads(prefix=path, before_write=lock.check)
        for key in self._store.iter_keys(prefix=path):
            self._delete(before_write=lock.check, key=key)
        self._store.remove_empty_folder_markers(prefix=path, before_write=lock.check)

    def _navigation(
        self,
        *,
        deployment_id: UUID,
        lock: CorpusPublicationLock | None = None,
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
        with (
            lock.transaction() if lock is not None else nullcontext() as connection,
            self._readers.documents(
                deployment_id=deployment_id, connection=connection
            ) as documents,
        ):
            desired = render_navigation(
                documents, policy=self.policy, ancestors=ancestors
            )
        before_write = lock.check if lock is not None else None
        for path in sorted(desired, key=lambda name: (-name.count("/"), name)):
            file = GeneratedFile(path, desired[path])
            if self._store.sha256_of(key=ObjectKey(path)) != file.sha256:
                self._put(path=path, file=file, before_write=before_write)
        for path in sorted(existing - desired.keys()):
            self._delete(key=ObjectKey(path), before_write=before_write)


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
