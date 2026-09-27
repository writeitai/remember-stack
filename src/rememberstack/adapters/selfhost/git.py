"""Plain local-Git remote plus D74 affected-path history erasure."""

from pathlib import Path
from pathlib import PurePosixPath
import shlex
import subprocess
from typing import Protocol
from uuid import UUID

from rememberstack.model import KRevision


class KPathCatalog(Protocol):
    """Resolve synced owner blockers and manifest-nominated Git paths."""

    def blocking_k_paths(self, *, deployment_id: UUID, doc_id: UUID) -> tuple[str, ...]:
        """Return owner-controlled paths with standing lineage citations."""
        ...

    def k_paths_for_artifacts(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> tuple[str, ...]:
        """Return exact current body and curation paths for affected artifacts."""
        ...


class LocalGitRepository:
    """Use one local working repository as self-host Plane-K truth."""

    def __init__(
        self,
        *,
        repository: Path,
        path_catalog: KPathCatalog,
        author_name: str,
        author_email: str,
    ) -> None:
        """Bind an existing clean repository and explicit commit identity."""
        self._repository = repository.resolve(strict=True)
        self._path_catalog = path_catalog
        self._author_name = author_name
        self._author_email = author_email
        if (
            _output(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "rev-parse",
                    "--is-bare-repository",
                )
            )
            == "true"
        ):
            raise ValueError("self-host Plane-K truth must be a working repository")

    def checkout(self, *, destination: Path) -> KRevision:
        """Clone the local truth repository into one driver-owned worktree."""
        _run(
            arguments=(
                "git",
                "clone",
                "--quiet",
                str(self._repository),
                str(destination),
            )
        )
        return KRevision(
            root=_output(arguments=("git", "-C", str(destination), "rev-parse", "HEAD"))
        )

    def publish(self, *, worktree: Path) -> KRevision:
        """Commit a prepared driver tree and push its current branch to local truth."""
        _run(arguments=("git", "-C", str(worktree), "add", "-A"))
        if (
            _status(
                arguments=("git", "-C", str(worktree), "diff", "--cached", "--quiet")
            )
            != 0
        ):
            _run(
                arguments=(
                    "git",
                    "-C",
                    str(worktree),
                    "-c",
                    f"user.name={self._author_name}",
                    "-c",
                    f"user.email={self._author_email}",
                    "commit",
                    "--quiet",
                    "-m",
                    "rememberstack knowledge update",
                )
            )
            _run(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "fetch",
                    "--quiet",
                    str(worktree),
                    "HEAD:refs/rememberstack/publish",
                )
            )
            _run(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "reset",
                    "--hard",
                    "refs/rememberstack/publish",
                )
            )
            _run(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "update-ref",
                    "-d",
                    "refs/rememberstack/publish",
                )
            )
        return KRevision(
            root=_output(arguments=("git", "-C", str(worktree), "rev-parse", "HEAD"))
        )

    def blocking_redaction_paths(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[str, ...]:
        """Delegate to current synced K ownership/citation state."""
        return self._path_catalog.blocking_k_paths(
            deployment_id=deployment_id, doc_id=doc_id
        )

    def purge_artifacts(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Erase affected paths from every ref and re-add sanitized current files."""
        current_paths = self._validated_paths(
            paths=self._path_catalog.k_paths_for_artifacts(
                deployment_id=deployment_id, artifact_ids=artifact_ids
            )
        )
        if not current_paths or self._honored(forget_id=forget_id, paths=current_paths):
            return
        paths = self._historical_paths(current_paths=current_paths)
        if _output(
            arguments=("git", "-C", str(self._repository), "status", "--porcelain")
        ):
            raise RuntimeError(
                "Plane-K repository must be clean before history erasure"
            )
        current = {
            path: target.read_bytes() if target.is_file() else None
            for path in current_paths
            for target in (self._repository / path,)
        }
        removal = "git rm -r --cached --ignore-unmatch -- " + " ".join(
            shlex.quote(path) for path in paths
        )
        _run(
            arguments=(
                "git",
                "-C",
                str(self._repository),
                "filter-branch",
                "--force",
                "--index-filter",
                removal,
                "--tag-name-filter",
                "cat",
                "--",
                "--all",
            )
        )
        self._drop_original_refs()
        for path, content in current.items():
            target = self._repository / path
            if content is None:
                target.unlink(missing_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        # filter-branch already removed these paths from the index. Deleted
        # current bodies must stay absent; git add rejects absent pathspecs.
        restored_paths = tuple(
            path for path, content in current.items() if content is not None
        )
        if restored_paths:
            _run(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "add",
                    "--",
                    *restored_paths,
                )
            )

        if (
            _status(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "diff",
                    "--cached",
                    "--quiet",
                )
            )
            != 0
        ):
            _run(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "-c",
                    f"user.name={self._author_name}",
                    "-c",
                    f"user.email={self._author_email}",
                    "commit",
                    "--quiet",
                    "-m",
                    f"hard-forget {forget_id}",
                )
            )
        head = _output(
            arguments=("git", "-C", str(self._repository), "rev-parse", "HEAD")
        )
        _run(
            arguments=(
                "git",
                "-C",
                str(self._repository),
                "update-ref",
                self._ack_ref(forget_id=forget_id),
                head,
            )
        )
        _run(
            arguments=(
                "git",
                "-C",
                str(self._repository),
                "reflog",
                "expire",
                "--expire=now",
                "--all",
            )
        )
        _run(arguments=("git", "-C", str(self._repository), "gc", "--prune=now"))

    def verify_artifacts_purged(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Prove affected current paths have only their sanitized post-purge history."""
        paths = self._validated_paths(
            paths=self._path_catalog.k_paths_for_artifacts(
                deployment_id=deployment_id, artifact_ids=artifact_ids
            )
        )
        if paths and not self._honored(forget_id=forget_id, paths=paths):
            raise RuntimeError(
                f"Plane-K purge verification failed for forget_id {forget_id}"
            )

    def _honored(self, *, forget_id: UUID, paths: tuple[str, ...]) -> bool:
        """Validate the store-local acknowledgement and single current path history."""
        if (
            _status(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "show-ref",
                    "--verify",
                    "--quiet",
                    self._ack_ref(forget_id=forget_id),
                )
            )
            != 0
        ):
            return False
        commits = _output(
            arguments=(
                "git",
                "-C",
                str(self._repository),
                "log",
                "--all",
                "--format=%H",
                "--",
                *paths,
            )
        ).splitlines()
        return len(set(commits)) <= 1

    def _validated_paths(self, *, paths: tuple[str, ...]) -> tuple[str, ...]:
        """Reject absolute/traversing Git paths before filesystem or shell use."""
        result: list[str] = []
        for value in sorted(set(paths)):
            path = PurePosixPath(value)
            if not value or path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe Plane-K path {value!r}")
            candidate = (self._repository / path).resolve()
            if not candidate.is_relative_to(self._repository):
                raise ValueError(f"Plane-K path {value!r} escapes the repository")
            result.append(path.as_posix())
        return tuple(result)

    def _historical_paths(self, *, current_paths: tuple[str, ...]) -> tuple[str, ...]:
        """Follow renames and return every historical name of affected files."""
        paths = set(current_paths)
        for current in current_paths:
            output = _output(
                arguments=(
                    "git",
                    "-C",
                    str(self._repository),
                    "log",
                    "--all",
                    "--follow",
                    "--name-status",
                    "--format=",
                    "--",
                    current,
                )
            )
            for line in output.splitlines():
                fields = line.split("\t")
                if len(fields) >= 2:
                    paths.update(fields[1:])
        return self._validated_paths(paths=tuple(paths))

    def _drop_original_refs(self) -> None:
        """Delete filter-branch backup refs so forgotten history is unreachable."""
        refs = _output(
            arguments=(
                "git",
                "-C",
                str(self._repository),
                "for-each-ref",
                "--format=%(refname)",
                "refs/original/",
            )
        ).splitlines()
        for ref in refs:
            _run(
                arguments=("git", "-C", str(self._repository), "update-ref", "-d", ref)
            )

    @staticmethod
    def _ack_ref(*, forget_id: UUID) -> str:
        """Return the receipt ref that disappears with an independently restored repo."""
        return f"refs/rememberstack/forget/{forget_id}"


def _run(*, arguments: tuple[str, ...]) -> None:
    """Run one Git command and preserve its full failing process exception."""
    subprocess.run(arguments, check=True, capture_output=True, text=True)


def _output(*, arguments: tuple[str, ...]) -> str:
    """Run one Git read and return its exact trimmed stdout."""
    return subprocess.run(
        arguments, check=True, capture_output=True, text=True
    ).stdout.strip()


def _status(*, arguments: tuple[str, ...]) -> int:
    """Run a Git predicate whose nonzero status is an expected false result."""
    return subprocess.run(
        arguments, check=False, capture_output=True, text=True
    ).returncode
