"""Pinned Workspace-Bench office skills staged into the local task workspace."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.hashing import sha256_bytes
from benchmarks.workspacebench.hashing import tree_digest
from benchmarks.workspacebench.models import OfficeSkillPin
from benchmarks.workspacebench.protocol import OFFICE_SKILL_SOURCE_RELPATH
from benchmarks.workspacebench.protocol import OFFICE_SKILL_WORKSPACE_RELPATH
from benchmarks.workspacebench.protocol import REQUIRED_OFFICE_EXECUTABLES


def office_skill_source(*, upstream: Path) -> Path:
    """Return the pinned upstream office-skill tree."""
    require_absolute_path(upstream, label="upstream checkout")
    source = upstream / OFFICE_SKILL_SOURCE_RELPATH
    if not source.is_dir():
        raise WorkspaceBenchError(
            f"pinned office skill tree missing: {OFFICE_SKILL_SOURCE_RELPATH}"
        )
    return source


def office_skill_digest(*, upstream: Path) -> str:
    """Content digest of the verified upstream office skill tree."""
    return tree_digest(office_skill_source(upstream=upstream))


def office_skill_children(*, upstream: Path) -> tuple[Path, ...]:
    """Return direct skill directories that each contain ``SKILL.md``."""
    source = office_skill_source(upstream=upstream)
    children: list[Path] = []
    for child in sorted(source.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.is_symlink():
            continue
        skill = child / "SKILL.md"
        if not skill.is_file() or skill.is_symlink():
            raise WorkspaceBenchError(
                f"office skill {child.name!r} is missing a regular SKILL.md"
            )
        children.append(child)
    if not children:
        raise WorkspaceBenchError(
            "pinned office skill tree has no discoverable SKILL.md children"
        )
    return tuple(children)


def stage_office_skills(*, upstream: Path, workspace: Path) -> str:
    """Copy each office skill to a discoverable ``.agents/skills/<name>`` child.

    Codex discovers skills as direct children of ``.agents/skills`` that contain
    ``SKILL.md``. Existing role-workspace skills are preserved. A conflicting
    non-identical name fails closed. The returned digest covers the staged
    office children only.
    """
    skills_root = workspace / OFFICE_SKILL_WORKSPACE_RELPATH
    skills_root.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for source_child in office_skill_children(upstream=upstream):
        destination = skills_root / source_child.name
        source_digest = tree_digest(source_child)
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise WorkspaceBenchError(
                    f"office skill name {source_child.name!r} conflicts with a "
                    "non-directory in the role workspace"
                )
            existing = tree_digest(destination)
            if existing != source_digest:
                raise WorkspaceBenchError(
                    f"office skill name {source_child.name!r} already exists in "
                    "the role workspace and is not identical to the pinned skill"
                )
            staged.append(destination)
            continue
        shutil.copytree(source_child, destination, symlinks=False)
        copied = tree_digest(destination)
        if copied != source_digest:
            raise WorkspaceBenchError(
                f"staged office skill {source_child.name!r} digest drifted"
            )
        staged.append(destination)
    hasher_payload = {
        child.name: tree_digest(child) for child in staged if child.is_dir()
    }
    encoded = json.dumps(hasher_payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(encoded)


def required_office_executables() -> tuple[str, ...]:
    """Host-local office binaries the skills invoke. Not Docker equivalence."""
    import shutil as which_shutil

    missing = [
        name for name in REQUIRED_OFFICE_EXECUTABLES if which_shutil.which(name) is None
    ]
    if missing:
        raise WorkspaceBenchError(
            "host-local office executables missing: "
            + ", ".join(missing)
            + ". Install LibreOffice (soffice) and Poppler (pdftoppm). "
            "This path does not claim Docker-container equivalence."
        )
    return REQUIRED_OFFICE_EXECUTABLES


def inspect_office(*, upstream: Path, skip_executables: bool = False) -> OfficeSkillPin:
    """Preflight office-skill pin. Executable checks are host-local only."""
    children = office_skill_children(upstream=upstream)
    digest = office_skill_digest(upstream=upstream)
    executables: tuple[str, ...] = ()
    if not skip_executables:
        executables = required_office_executables()
    return OfficeSkillPin(
        source_relpath=OFFICE_SKILL_SOURCE_RELPATH,
        workspace_relpath=OFFICE_SKILL_WORKSPACE_RELPATH,
        digest=digest,
        executables=executables,
        skill_names=tuple(child.name for child in children),
    )
