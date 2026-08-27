"""D74 purge acknowledgement for self-host projection bytes and caches."""

from pathlib import Path
from pathlib import PurePosixPath
import shutil
from uuid import UUID

from rememberstack.model import ObjectKey
from rememberstack.ports import ObjectPurgePort
from rememberstack.spine import ProjectionCatalog


class SelfHostProjectionPurger:
    """Erase old durable P3 prefixes, registry rows, and local serving copies."""

    def __init__(
        self,
        *,
        object_purger: ObjectPurgePort,
        catalog: ProjectionCatalog,
        mount_root: Path,
    ) -> None:
        """Bind explicit durable and local-cache roots without inventing providers."""
        self._object_purger = object_purger
        self._catalog = catalog
        self._mount_root = mount_root.resolve()

    def purge_projections(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Idempotently erase every manifest-nominated old projection copy."""
        self._object_purger.purge_objects(keys=(), prefixes=prefixes)
        self._catalog.purge_snapshot_prefixes(
            deployment_id=deployment_id,
            prefixes=tuple(prefix.root for prefix in prefixes),
        )
        for prefix in prefixes:
            for path in self._serving_paths(deployment_id=deployment_id, prefix=prefix):
                self._remove(path=path)

    def verify_projections_purged(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        """Prove durable bytes, registry rows, caches, and mounts are absent."""
        self._object_purger.verify_objects_purged(keys=(), prefixes=prefixes)
        roots = tuple(prefix.root for prefix in prefixes)
        if self._catalog.snapshot_prefixes_exist(
            deployment_id=deployment_id, prefixes=roots
        ):
            raise RuntimeError("projection purge verification found registry rows")
        remaining = [
            str(path)
            for prefix in prefixes
            for path in self._serving_paths(deployment_id=deployment_id, prefix=prefix)
            if path.exists() or path.is_symlink()
        ]
        if remaining:
            raise RuntimeError(
                f"projection purge verification found serving copies: {remaining!r}"
            )

    def _serving_paths(self, *, deployment_id: UUID, prefix: ObjectKey) -> tuple[Path]:
        """Return the exact P3 versioned mount for one prefix."""
        version = PurePosixPath(prefix.root).name
        return (self._mount_root / str(deployment_id) / f"p3-{version}",)

    @staticmethod
    def _remove(*, path: Path) -> None:
        """Remove one exact serving copy, treating absence as success."""
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
