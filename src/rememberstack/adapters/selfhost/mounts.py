"""Local-directory mount publisher: the four D51 read-only views (e0 §5).

Agents read the memory on their filesystem: the **corpus filesystem** they
browse first, the **artifacts** they drill into from a stub, the **raw**
originals — deliberately *off the navigation path* — and the Plane-K
checkout. Views are intended for read-only use; external bucket mounts must enforce
read-only access and provider audit logging. This publisher does not enforce
filesystem permissions. Writes go through the pipeline; Postgres is authority.

The self-host tier materializes what the cloud tier gets from a bucket
mount. D51 has three guardrails, with external mount enforcement owned by
the operator:

1. **Raw is off-path.** Nothing in the corpus tree links into it; reaching
   an original means following an explicit `raw_uri` from a stub or from
   `document.md` frontmatter — a deliberate act, never a browse default.
2. **Data-access audit logging is mandatory.** The audit property came
   from logging, not from keeping raw unmounted (a mount read is still a
   read). External raw mounts require provider audit logging. Programmatic
   local reads can use `AuditedRawReader`, which refuses an unattributed
   request rather than logging a blank; it does not intercept filesystem reads.
3. **Storage class routes by mime.** Media a multimodal harness actually
   reads stays hot; text/office originals kept only for audit and
   re-conversion go cold — this kills the grep-the-archive cost bug at the
   source. Per-deployment config, like the D38 converter router.

The corpus view always serves the snapshot the registry marks latest, and
swaps whole trees atomically, so a browsing agent never walks a half-built
tree.
"""

from datetime import datetime
from datetime import UTC
import json
import os
from pathlib import Path
import shutil
from typing import Final
from typing import Protocol
from uuid import UUID
from uuid import uuid4

from rememberstack.core import storage_class_for
from rememberstack.model import ObjectKey
from rememberstack.model import PublishedMounts
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.projection import ProjectionCatalog

EMPTY_CORPUS_NOTE: Final = (
    "# Corpus filesystem\n\n"
    "> No P3 snapshot has been published yet. Run the corpus-filesystem\n"
    "> builder; until then use the query API, which needs no projection.\n"
)


class RawAccessDenied(Exception):
    """An unattributed raw read was refused (originals are audited)."""


class MountAdmission(Protocol):
    """The composition-owned barrier checked before publishing serving paths."""

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Raise while D74 keeps the deployment fail-closed."""
        ...


class LocalMountPublisher:
    """Publish the P3, artifact, raw, and Plane-K views as local trees."""

    def __init__(
        self,
        *,
        root: Path,
        catalog: ProjectionCatalog | None = None,
        corpusfs_store: ObjectStorePort | None = None,
        artifacts_root: Path | None = None,
        raw_root: Path | None = None,
        knowledge_root: Path | None = None,
        admission: MountAdmission,
    ) -> None:
        """Bind the publisher to its mount root, the P3 source, and the stores.

        The artifact/raw/knowledge views point at the REAL store roots when
        given; configured roots must already exist (an empty directory is not a
        usable mount). External mounts must enforce read-only access and audit
        raw reads; ``PublishedMounts.read_only`` declares the usage contract,
        not filesystem permission enforcement.
        Without them the publisher provisions empty view roots — the
        Phase-0 shape, still useful before any store exists.
        """
        self._root = root.resolve()
        self._catalog = catalog
        self._corpusfs_store = corpusfs_store
        self._artifacts_root = artifacts_root
        self._raw_root = raw_root
        self._knowledge_root = knowledge_root
        self._admission = admission

    def publish(self, *, deployment_id: UUID) -> PublishedMounts:
        """Publish and return the exact four read-only deployment views."""
        self._admission.assert_available(deployment_id=deployment_id)
        for store_root in (self._artifacts_root, self._raw_root, self._knowledge_root):
            if store_root is not None and not store_root.is_dir():
                raise ValueError(
                    f"configured mount root is not an existing directory: {store_root}"
                )
        base = self._root / str(deployment_id)
        base.mkdir(parents=True, exist_ok=True)
        corpus = base / "p3"
        self._materialize_corpus(deployment_id=deployment_id, link=corpus)
        return PublishedMounts(
            deployment_id=deployment_id,
            p3=str(corpus),
            artifacts=str(
                self._view(base=base, name="artifacts", real=self._artifacts_root)
            ),
            # off the navigation path (D51): the tree never promotes raw —
            # stubs carry an explicit pointer. Provider mounts must audit
            # reads; AuditedRawReader audits programmatic local access.
            raw=str(self._view(base=base, name="raw", real=self._raw_root)),
            knowledge=str(
                self._view(base=base, name="knowledge", real=self._knowledge_root)
            ),
            read_only=True,
        )

    def _view(self, *, base: Path, name: str, real: Path | None) -> Path:
        """One view locator: the real store root when known, else an empty dir."""
        if real is not None:
            if not real.is_dir():
                raise ValueError(
                    f"configured mount root is not an existing directory: {real}"
                )
            return real.resolve()
        placeholder = base / name
        placeholder.mkdir(parents=True, exist_ok=True)
        return placeholder

    def _materialize_corpus(self, *, deployment_id: UUID, link: Path) -> None:
        """Serve the LATEST PUBLISHED snapshot behind an ATOMIC pointer.

        The mount path is a symlink to a versioned directory, and the swap
        replaces that symlink with `os.replace` — atomic on POSIX. The
        previous "rmtree then rename" left a window where the mount path
        did not exist at all, so a reader could hit ENOENT mid-swap and two
        publishers could delete each other's staging (Codex review).
        """
        if self._catalog is None or self._corpusfs_store is None:
            link.mkdir(parents=True, exist_ok=True)
            return
        latest = self._catalog.latest_snapshot(
            deployment_id=deployment_id, plane="P3_corpusfs"
        )
        if latest is None:
            empty = link.parent / "p3-empty"
            empty.mkdir(parents=True, exist_ok=True)
            (empty / "llms.txt").write_text(EMPTY_CORPUS_NOTE, encoding="utf-8")
            _point(link=link, target=empty)
            return
        version = str(latest["version"])
        served = link.parent / f"p3-{version}"
        if not (served / ".snapshot-version").exists():
            prefix = str(latest["gcs_uri"])
            manifest = json.loads(
                self._corpusfs_store.read_bytes(
                    key=ObjectKey(f"{prefix}/MANIFEST.json")
                )
            )
            # a unique staging dir per publisher: concurrent publishes of
            # the same version never delete each other's work
            staging = link.parent / f".staging-{version}-{uuid4().hex[:8]}"
            for relative in manifest["files"]:
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(
                    self._corpusfs_store.read_bytes(
                        key=ObjectKey(f"{prefix}/{relative}")
                    )
                )
            (staging / ".snapshot-version").write_text(version, encoding="utf-8")
            try:
                staging.rename(served)  # atomic: the version dir appears whole
            except OSError:  # another publisher won the race — theirs is fine
                shutil.rmtree(staging, ignore_errors=True)
        _point(link=link, target=served)


class AuditedRawReader:
    """The ONLY way to read an original — because reads must be logged.

    D51's audit property comes from logging, not from keeping raw
    unmounted: a mount read is still a read. So raw access is offered
    exclusively through this reader, which records the accessor and the
    stated purpose before returning bytes and refuses an unattributed
    request outright rather than logging a blank.
    """

    def __init__(self, *, raw_store: ObjectStorePort, audit_log: Path) -> None:
        """Bind the reader to the raw store and its append-only audit log."""
        self._raw_store = raw_store
        self._audit_log = audit_log

    def read(
        self, *, deployment_id: UUID, raw_uri: str, accessor: str, purpose: str
    ) -> bytes:
        """Read one original, recording who read it and why."""
        if not accessor.strip() or not purpose.strip():
            raise RawAccessDenied(
                "raw access requires an accessor and a stated purpose:"
                " originals are audited, never anonymously readable"
            )
        content = self._raw_store.read_bytes(key=ObjectKey(raw_uri))
        self._append(
            {
                "at": datetime.now(tz=UTC).isoformat(),
                "deployment_id": str(deployment_id),
                "raw_uri": raw_uri,
                "accessor": accessor,
                "purpose": purpose,
                "bytes": len(content),
            }
        )
        return content

    def entries(self) -> tuple[dict[str, object], ...]:
        """The audit trail, oldest first (the operator's read surface)."""
        if not self._audit_log.exists():
            return ()
        return tuple(
            json.loads(line)
            for line in self._audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    def _append(self, entry: dict[str, object]) -> None:
        """Append one audit record (the log is append-only by construction)."""
        self._audit_log.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def _point(*, link: Path, target: Path) -> None:
    """Atomically point the mount path at a versioned directory.

    A symlink swapped with `os.replace` is atomic on POSIX: a reader either
    sees the old snapshot or the new one, never a missing path.
    """
    staging_link = link.with_name(f".{link.name}-{uuid4().hex[:8]}")
    staging_link.symlink_to(target, target_is_directory=True)
    if link.exists() and not link.is_symlink():
        shutil.rmtree(link)  # a legacy real directory: replaced once
    os.replace(staging_link, link)


__all__ = (
    "AuditedRawReader",
    "EMPTY_CORPUS_NOTE",
    "LocalMountPublisher",
    "RawAccessDenied",
    "storage_class_for",  # re-exported: the adapter applies this policy
)
