"""Local-filesystem object store adapter: immutable bytes under one root (D61/D62)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectKeyEscapesRootError
from rememberstack.model import SourceRangeError


class LocalFSObjectStore:
    """The self-host object store: one directory tree of immutable objects."""

    def __init__(self, *, root: Path) -> None:
        """Bind the store to its root directory, creating it if absent."""
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        """Read all bytes stored under an existing object key."""
        return self._path_for(key=key).read_bytes()

    @contextmanager
    def open_stream(
        self, *, key: ObjectKey, chunk_bytes: int = 1024 * 1024
    ) -> Iterator[Iterator[bytes]]:
        """Open an ordered chunked read, closing the file when the block exits."""
        if chunk_bytes <= 0:
            raise SourceRangeError(f"chunk_bytes must be positive, got {chunk_bytes}")
        with self._path_for(key=key).open(mode="rb") as handle:

            def chunks() -> Iterator[bytes]:
                """Yield successive fixed-size reads until the file is spent."""
                while chunk := handle.read(chunk_bytes):
                    yield chunk

            yield chunks()

    def read_range(self, *, key: ObjectKey, start: int, end: int) -> bytes:
        """Read the half-open byte interval ``[start, end)`` of one object."""
        if start < 0 or end <= start:
            raise SourceRangeError(
                f"range [{start}, {end}) is empty, reversed, or negative"
            )
        with self._path_for(key=key).open(mode="rb") as handle:
            handle.seek(start)
            content = handle.read(end - start)
        if len(content) != end - start:
            raise SourceRangeError(
                f"range [{start}, {end}) of {key.root!r} returned {len(content)} "
                f"bytes, not the {end - start} requested"
            )
        return content

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str | None = None
    ) -> None:
        """Create immutable bytes, failing rather than replacing an occupied key.

        A local filesystem has no storage classes, so the D51 routing
        decision is RECORDED beside the object instead of dropped — the
        cloud adapter turns the same value into a real class, and either
        way an operator can see what each original was routed to.
        """
        path = self._path_for(key=key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open(mode="xb") as handle:
                handle.write(content)
        except FileExistsError as err:
            raise ObjectAlreadyExistsError(
                f"object key {key.root!r} is already occupied; objects are immutable"
            ) from err
        if storage_class is not None:
            path.with_name(f"{path.name}.storage-class").write_text(
                storage_class, encoding="utf-8"
            )

    def storage_class_of(self, *, key: ObjectKey) -> str | None:
        """The class one object was routed to, when the writer declared it."""
        marker = self._path_for(key=key).with_suffix("")
        marker = self._path_for(key=key)
        marker = marker.with_name(f"{marker.name}.storage-class")
        return marker.read_text(encoding="utf-8") if marker.exists() else None

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Idempotently erase exact keys, storage markers, and prefix matches."""
        exact_paths = tuple(self._path_for(key=key) for key in keys)
        normalized_prefixes = tuple(
            self._path_for(key=prefix).relative_to(self._root).as_posix()
            for prefix in prefixes
        )
        for path in exact_paths:
            path.unlink(missing_ok=True)
            path.with_name(f"{path.name}.storage-class").unlink(missing_ok=True)
        if normalized_prefixes:
            for path in tuple(self._root.rglob("*")):
                if not (path.is_file() or path.is_symlink()):
                    continue
                relative = path.relative_to(self._root).as_posix()
                if any(
                    relative == prefix.rstrip("/")
                    or relative.startswith(f"{prefix.rstrip('/')}/")
                    for prefix in normalized_prefixes
                ):
                    path.unlink(missing_ok=True)
        self._remove_empty_directories()

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Prove exact keys and prefix-boundary descendants are absent."""
        remaining = [
            key.root
            for key in keys
            if self._path_for(key=key).exists()
            or self._path_for(key=key)
            .with_name(f"{self._path_for(key=key).name}.storage-class")
            .exists()
        ]
        normalized_prefixes = tuple(
            self._path_for(key=prefix).relative_to(self._root).as_posix().rstrip("/")
            for prefix in prefixes
        )
        for path in self._root.rglob("*"):
            if not (path.is_file() or path.is_symlink()):
                continue
            relative = path.relative_to(self._root).as_posix()
            if any(
                relative == prefix or relative.startswith(f"{prefix}/")
                for prefix in normalized_prefixes
            ):
                remaining.append(relative)
        if remaining:
            raise RuntimeError(
                f"object purge verification found: {sorted(remaining)!r}"
            )

    def _path_for(self, *, key: ObjectKey) -> Path:
        """Resolve a key to a path strictly inside the root (no traversal)."""
        candidate = (self._root / key.root).resolve()
        if not candidate.is_relative_to(self._root):
            raise ObjectKeyEscapesRootError(
                f"object key {key.root!r} escapes the store root"
            )
        return candidate

    def _remove_empty_directories(self) -> None:
        """Prune empty object-key parents without ever removing the store root."""
        directories = sorted(
            (
                path
                for path in self._root.rglob("*")
                if path.is_dir() and not path.is_symlink()
            ),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            if not any(directory.iterdir()):
                directory.rmdir()
