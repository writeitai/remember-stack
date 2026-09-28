"""Pinned external Workspace-Bench checkout inspection. Read-only."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path
import subprocess

from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.hashing import sha256_file
from benchmarks.workspacebench.models import UpstreamInspection
from benchmarks.workspacebench.protocol import EXPECTED_UPSTREAM_FILE_SHA256
from benchmarks.workspacebench.protocol import UPSTREAM_COMMIT

GitCommand = Callable[[Sequence[str], Path], str]


def inspect_upstream(
    *, path: Path, expected_commit: str = UPSTREAM_COMMIT, git: GitCommand | None = None
) -> UpstreamInspection:
    """Require the exact upstream commit and a clean worktree."""
    require_absolute_path(path, label="upstream checkout")
    if not path.is_dir():
        raise WorkspaceBenchError(f"upstream checkout is not a directory: {path}")
    runner = git if git is not None else _run_git
    commit = runner(["rev-parse", "HEAD"], path).strip()
    dirty_output = runner(["status", "--porcelain"], path)
    dirty = bool(dirty_output.strip())
    file_sha256: dict[str, str] = {}
    failures: list[str] = []
    if commit != expected_commit:
        failures.append(
            f"upstream commit {commit} does not match pin {expected_commit}"
        )
    if dirty:
        failures.append("upstream checkout is dirty; refusing to use a moving tree")
    for relative, expected in EXPECTED_UPSTREAM_FILE_SHA256.items():
        candidate = path / relative
        if not candidate.is_file():
            failures.append(f"required post-leakage runner file missing: {relative}")
            continue
        observed = sha256_file(candidate)
        file_sha256[relative] = observed
        if observed != expected:
            failures.append(
                f"upstream file hash drift for {relative}: expected {expected}, "
                f"observed {observed}"
            )
    if failures:
        raise WorkspaceBenchError("; ".join(failures))
    return UpstreamInspection(
        path=str(path), commit=commit, dirty=False, file_sha256=file_sha256
    )


def _run_git(args: Sequence[str], cwd: Path) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 -- git argv is fixed by caller
            ["git", *args], cwd=str(cwd), check=False, capture_output=True, text=True
        )
    except OSError as error:
        raise WorkspaceBenchError(
            f"git is required to inspect upstream: {error}"
        ) from error
    if completed.returncode != 0:
        raise WorkspaceBenchError(
            completed.stderr.strip() or f"git {' '.join(args)} failed"
        )
    return completed.stdout
