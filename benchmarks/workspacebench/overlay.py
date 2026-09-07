"""Hashed overlays applied only to a disposable copy of the pinned checkout."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import sha256_bytes
from benchmarks.workspacebench.hashing import sha256_file

_OVERLAY_DIR: Final = Path(__file__).resolve().parent / "overlays"
_MANIFEST_NAME: Final = "manifest.json"


class OverlayEntry(BaseModel):
    """One exact-hash overlay against a pinned upstream file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_relpath: str = Field(min_length=1)
    expected_target_sha256: str = Field(min_length=64, max_length=64)
    overlay_relpath: str = Field(min_length=1)
    overlay_sha256: str = Field(min_length=64, max_length=64)


class OverlayManifest(BaseModel):
    """Complete overlay set included in the protocol fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "WorkspaceBenchOverlayManifest/v1"
    overlays: tuple[OverlayEntry, ...] = ()


def overlay_dir() -> Path:
    """Return the in-repo overlay directory."""
    return _OVERLAY_DIR


def load_overlay_manifest(*, directory: Path | None = None) -> OverlayManifest:
    """Load the hashed overlay manifest shipped with this adapter."""
    root = directory if directory is not None else _OVERLAY_DIR
    path = root / _MANIFEST_NAME
    if not path.is_file():
        raise WorkspaceBenchError(f"overlay manifest missing: {path}")
    return OverlayManifest.model_validate_json(path.read_text(encoding="utf-8"))


def overlay_manifest_sha256(*, directory: Path | None = None) -> str:
    """Fingerprint the overlay manifest bytes."""
    root = directory if directory is not None else _OVERLAY_DIR
    return sha256_file(root / _MANIFEST_NAME)


def apply_overlays(
    *,
    source_checkout: Path,
    destination: Path,
    manifest: OverlayManifest | None = None,
    overlay_root: Path | None = None,
) -> Path:
    """Copy ``source_checkout`` to ``destination`` and apply exact-hash overlays.

    The source checkout is never modified. Target-file hash drift fails closed
    instead of fuzzy-applying.
    """
    if destination.exists():
        raise WorkspaceBenchError(f"overlay destination already exists: {destination}")
    _copy_tree(source=source_checkout, destination=destination)
    resolved_manifest = (
        manifest
        if manifest is not None
        else load_overlay_manifest(directory=overlay_root)
    )
    root = overlay_root if overlay_root is not None else _OVERLAY_DIR
    for entry in resolved_manifest.overlays:
        target = destination / entry.target_relpath
        if not target.is_file():
            raise WorkspaceBenchError(
                f"overlay target missing in disposable copy: {entry.target_relpath}"
            )
        observed = sha256_file(target)
        if observed != entry.expected_target_sha256:
            raise WorkspaceBenchError(
                f"overlay target hash drift for {entry.target_relpath}: "
                f"expected {entry.expected_target_sha256}, observed {observed}"
            )
        overlay_path = root / entry.overlay_relpath
        overlay_bytes = overlay_path.read_bytes()
        overlay_hash = sha256_bytes(overlay_bytes)
        if overlay_hash != entry.overlay_sha256:
            raise WorkspaceBenchError(
                f"overlay payload hash drift for {entry.overlay_relpath}"
            )
        target.write_bytes(overlay_bytes)
    return destination


def _copy_tree(*, source: Path, destination: Path) -> None:
    import shutil

    shutil.copytree(source, destination, symlinks=True, ignore=_ignore_git)


def _ignore_git(directory: str, names: list[str]) -> set[str]:
    del directory
    return {".git"} if ".git" in names else set()
