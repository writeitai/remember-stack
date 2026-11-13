"""Local object storage: immutable E0 creation and atomic corpus replacement."""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from rememberstack.core.object_keys import matches_object_prefix
from rememberstack.core.object_keys import validate_object_path
from rememberstack.core.storage_routing import COLD
from rememberstack.core.storage_routing import HOT
from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectKeyEscapesRootError
from rememberstack.model import ObjectPurgeEvidence


class LocalFSObjectStore:
    """One reader tree, with private class metadata in a sibling directory.

    The root and its sibling must be on the same filesystem for atomic writes.
    No sidecars or temporary uploads appear inside the reader tree.
    """

    def __init__(self, *, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._metadata_root = self._root.with_name(f".{self._root.name}.metadata")
        self._metadata_root.mkdir(parents=True, exist_ok=True)

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Read all bytes stored under an existing object key."""
        return self._path_for(key=key).read_bytes()

    def iter_bytes(self, *, key: ObjectKey, chunk_size: int) -> Iterator[bytes]:
        """Stream one object's bytes without a whole-object allocation."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        with self._path_for(key=key).open("rb") as handle:
            while chunk := handle.read(chunk_size):
                yield chunk

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str
    ) -> None:
        """Create complete immutable bytes; an occupied key is never replaced."""
        self._write(
            key=key, chunks=(content,), storage_class=storage_class, replace=False
        )

    def replace_stream(
        self,
        *,
        key: ObjectKey,
        chunks: Iterable[bytes],
        storage_class: str,
        sha256: str | None = None,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        """Publish a complete temporary file only after normal input exhaustion."""
        self._write(
            key=key,
            chunks=chunks,
            storage_class=storage_class,
            replace=True,
            sha256=sha256,
            before_write=before_write,
        )

    def _write(
        self,
        *,
        key: ObjectKey,
        chunks: Iterable[bytes],
        storage_class: str,
        replace: bool,
        sha256: str | None = None,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        if storage_class not in {HOT, COLD}:
            raise ValueError("invalid storage class")
        path = self._path_for(key=key)
        marker = self._class_path(key=key)
        path.parent.mkdir(parents=True, exist_ok=True)
        marker.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        source = iter(chunks)
        try:
            with NamedTemporaryFile(dir=self._metadata_root, delete=False) as handle:
                temporary = Path(handle.name)
                for chunk in source:
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if before_write:
                before_write()
            if replace:
                temporary.replace(path)
            else:
                try:
                    os.link(temporary, path)
                except FileExistsError as error:
                    raise ObjectAlreadyExistsError(
                        f"object key {key.root!r} is already occupied; objects are immutable"
                    ) from error
            marker.write_text(storage_class, encoding="utf-8")
            digest = self._digest_path(key=key)
            if sha256 is not None:
                digest.parent.mkdir(parents=True, exist_ok=True)
                digest.write_text(sha256, encoding="ascii")
            else:
                digest.unlink(missing_ok=True)
        finally:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            finally:
                close = getattr(source, "close", None)
                if close is not None:
                    close()

    def storage_class_of(self, *, key: ObjectKey) -> str | None:
        """The declared class, kept outside the mounted reader paths."""
        marker = self._class_path(key=key)
        return marker.read_text(encoding="utf-8") if marker.exists() else None

    def abort_uploads(
        self, *, prefix: str, before_write: Callable[[], None]
    ) -> ObjectPurgeEvidence:
        """Local temporary files are private and removed by their owning writer."""
        validate_object_path(prefix, prefix=True)
        return ObjectPurgeEvidence(multipart_uploads="checked")

    def sha256_of(self, *, key: ObjectKey) -> str | None:
        """Read private digest metadata without putting sidecars in reader paths."""
        digest = self._digest_path(key=key)
        return (
            digest.read_text(encoding="ascii")
            if self._path_for(key=key).is_file() and digest.is_file()
            else None
        )

    def iter_keys(self, *, prefix: str) -> Iterator[ObjectKey]:
        """Stream files at or beneath a safe prefix, including the empty prefix."""
        boundary = validate_object_path(prefix, prefix=True)
        start = self._path_for(key=ObjectKey(boundary)) if boundary else self._root
        if start.is_file():
            yield ObjectKey(boundary)
        elif start.is_dir():
            for path in start.rglob("*"):
                if path.is_file():
                    key = ObjectKey(path.relative_to(self._root).as_posix())
                    self._path_for(key=key)  # Refuse symlinks escaping the reader tree.
                    yield key

    def delete(self, *, key: ObjectKey) -> None:
        """Erase one exact object and its class; a directory is not an object."""
        path = self._path_for(key=key)
        if not path.is_dir():
            path.unlink(missing_ok=True)
        marker = self._class_path(key=key)
        if not marker.is_dir():
            marker.unlink(missing_ok=True)
        digest = self._digest_path(key=key)
        if not digest.is_dir():
            digest.unlink(missing_ok=True)
        self._prune_parents(digest.parent, root=self._metadata_root)
        self._prune_parents(path.parent, root=self._root)
        self._prune_parents(self._class_path(key=key).parent, root=self._metadata_root)

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Use exact deletion for boundary-safe, idempotent erasure."""
        for key in keys:
            self._path_for(key=key)
        boundaries = tuple(validate_object_path(p.root, prefix=True) for p in prefixes)
        for key in keys:
            self.delete(key=key)
        for boundary in boundaries:
            for key in self.iter_keys(prefix=boundary):
                self.delete(key=key)
        # Include orphaned class records when object bytes are already absent.
        for key in self._iter_class_keys():
            if any(matches_object_prefix(key.root, prefix=p) for p in boundaries):
                self.delete(key=key)

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Verify both reader bytes and private class records are absent."""
        remaining = [
            key.root
            for key in keys
            if self._path_for(key=key).is_file() or self._class_path(key=key).exists()
        ]
        boundaries = tuple(validate_object_path(p.root, prefix=True) for p in prefixes)
        for boundary in boundaries:
            remaining.extend(key.root for key in self.iter_keys(prefix=boundary))
        remaining.extend(
            key.root
            for key in self._iter_class_keys()
            if any(matches_object_prefix(key.root, prefix=p) for p in boundaries)
        )
        if remaining:
            raise RuntimeError(
                f"object purge verification found: {sorted(remaining)!r}"
            )

    def _path_for(self, *, key: ObjectKey) -> Path:
        """Resolve only canonical relative keys, never traversal or external links."""
        name = validate_object_path(key.root)
        candidate = (self._root / name).resolve()
        if not candidate.is_relative_to(self._root):
            raise ObjectKeyEscapesRootError(
                f"object key {name!r} escapes the store root"
            )
        return candidate

    def _class_path(self, *, key: ObjectKey) -> Path:
        return self._metadata_root / "classes" / validate_object_path(key.root)

    def _digest_path(self, *, key: ObjectKey) -> Path:
        return self._metadata_root / "digests" / validate_object_path(key.root)

    def _iter_class_keys(self) -> Iterator[ObjectKey]:
        root = self._metadata_root / "classes"
        for path in root.rglob("*"):
            if path.is_file():
                yield ObjectKey(path.relative_to(root).as_posix())

    @staticmethod
    def _prune_parents(path: Path, *, root: Path) -> None:
        while path != root and path.is_relative_to(root):
            try:
                path.rmdir()
            except (FileNotFoundError, OSError):
                return
            path = path.parent
