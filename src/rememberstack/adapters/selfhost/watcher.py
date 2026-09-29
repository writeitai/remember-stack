"""The local-directory watched source: watch a folder on the self-host tier.

The genuinely useful self-host connector (and the lifecycle machinery's test
double with real semantics): source_ref = the file's relative path, revision
= mtime+size, deletion = the file is gone. Cloud connectors (Drive) implement
the same port behind their provider adapters.
"""

from collections.abc import Mapping
from datetime import datetime
from datetime import UTC
from pathlib import Path

from rememberstack.model import SourceItem

_MIME_BY_SUFFIX = {".md": "text/markdown", ".txt": "text/plain", ".html": "text/html"}


class LocalDirectoryWatcher:
    """Watch one directory tree of text documents."""

    def __init__(self, *, root: Path) -> None:
        """Bind the watcher to its directory (created if absent)."""
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def poll(self, *, known: Mapping[str, str]) -> tuple[SourceItem, ...]:
        """Every current file, plus deletion markers for vanished refs.

        Paths that resolve outside the root (symlinks pointing elsewhere)
        are skipped: a writable watched directory must not become a lever
        for ingesting arbitrary readable files into memory.
        """
        base = self._root.resolve()
        items: list[SourceItem] = []
        seen: set[str] = set()
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix not in _MIME_BY_SUFFIX:
                continue
            if not path.resolve().is_relative_to(base):
                continue  # a symlink escaping the root is not a watched file
            ref = path.relative_to(self._root).as_posix()
            seen.add(ref)
            stat = path.stat()
            items.append(
                SourceItem(
                    source_ref=ref,
                    revision=f"{stat.st_mtime_ns}:{stat.st_size}",
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    filename=path.name,
                    mime=_MIME_BY_SUFFIX[path.suffix],
                    source_path=ref,
                )
            )
        for ref in sorted(set(known) - seen):
            items.append(
                SourceItem(
                    source_ref=ref,
                    revision="deleted",
                    modified_at=datetime.now(tz=UTC),
                    deleted=True,
                )
            )
        return tuple(items)

    def fetch(self, *, source_ref: str) -> bytes:
        """The file's current bytes; refs escaping the root are refused."""
        path = (self._root / source_ref).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise PermissionError(f"{source_ref!r} escapes the watched root")
        return path.read_bytes()
