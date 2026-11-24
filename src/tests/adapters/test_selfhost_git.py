"""Real-Git proofs for the self-host D74 affected-path history purge."""

from pathlib import Path
import subprocess
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import KGitRemotePort

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")
_DOC_ID = UUID("74000000-0000-0000-0000-000000000002")
_FORGET_ID = UUID("74000000-0000-0000-0000-000000000003")
_ARTIFACT_ID = UUID("74000000-0000-0000-0000-000000000004")


class PathCatalog:
    """Return fixed affected artifact paths."""

    def __init__(self, *, paths: tuple[str, ...]) -> None:
        self.paths = paths

    def k_paths_for_artifacts(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> tuple[str, ...]:
        return self.paths


def _repository(*, root: Path) -> Path:
    """Create renamed private history plus a sanitized current K path."""
    repository = root / "knowledge"
    _git("init", "--quiet", "-b", "main", str(repository))
    _git("-C", str(repository), "config", "user.name", "Fixture")
    _git("-C", str(repository), "config", "user.email", "fixture@example.test")
    legacy = repository / "K1/legacy.md"
    affected = repository / "K1/affected.md"
    unrelated = repository / "K1/unrelated.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("private UNIQUE_FORGET_TOKEN v1\n", encoding="utf-8")
    unrelated.write_text("unrelated v1\n", encoding="utf-8")
    _commit(repository=repository, message="initial")
    legacy.write_text("private UNIQUE_FORGET_TOKEN v2\n", encoding="utf-8")
    unrelated.write_text("unrelated v2\n", encoding="utf-8")
    _commit(repository=repository, message="update")
    _git("-C", str(repository), "mv", "K1/legacy.md", "K1/affected.md")
    _commit(repository=repository, message="rename affected page")
    affected.write_text("sanitized current page\n", encoding="utf-8")
    _commit(repository=repository, message="redact and recompile")
    return repository


def test_local_git_purge_erases_affected_history_and_keeps_clean_current(
    tmp_path: Path,
) -> None:
    """Remove private blobs from every ref without discarding unrelated path history."""
    repository = _repository(root=tmp_path)
    adapter = LocalGitRepository(
        repository=repository,
        path_catalog=PathCatalog(paths=("K1/affected.md",)),
        author_name="RememberStack",
        author_email="rememberstack@example.test",
    )
    purge: KGitPurgePort = adapter

    purge.purge_artifacts(
        deployment_id=_DEPLOYMENT_ID, forget_id=_FORGET_ID, artifact_ids=(_ARTIFACT_ID,)
    )
    purge.verify_artifacts_purged(
        deployment_id=_DEPLOYMENT_ID, forget_id=_FORGET_ID, artifact_ids=(_ARTIFACT_ID,)
    )
    first_head = _output("-C", str(repository), "rev-parse", "HEAD")
    purge.purge_artifacts(
        deployment_id=_DEPLOYMENT_ID, forget_id=_FORGET_ID, artifact_ids=(_ARTIFACT_ID,)
    )

    assert (repository / "K1/affected.md").read_text(encoding="utf-8") == (
        "sanitized current page\n"
    )
    assert not (repository / "K1/legacy.md").exists()
    assert (
        _output(
            "-C",
            str(repository),
            "log",
            "--all",
            "-S",
            "UNIQUE_FORGET_TOKEN",
            "--format=%H",
        )
        == ""
    )
    assert (
        len(
            _output(
                "-C",
                str(repository),
                "log",
                "--all",
                "--format=%H",
                "--",
                "K1/affected.md",
            ).splitlines()
        )
        == 1
    )
    assert (
        len(
            _output(
                "-C",
                str(repository),
                "log",
                "--all",
                "--format=%H",
                "--",
                "K1/unrelated.md",
            ).splitlines()
        )
        == 2
    )
    assert (
        _output(
            "-C",
            str(repository),
            "rev-parse",
            f"refs/rememberstack/forget/{_FORGET_ID}",
        )
        == first_head
    )
    assert _output("-C", str(repository), "rev-parse", "HEAD") == first_head
    assert (
        _output(
            "-C",
            str(repository),
            "for-each-ref",
            "--format=%(refname)",
            "refs/original/",
        )
        == ""
    )


def test_local_git_remote_checkout_and_publish_update_truth(tmp_path: Path) -> None:
    """Keep ordinary K publication usable through the same local adapter."""
    repository = _repository(root=tmp_path)
    adapter: KGitRemotePort = LocalGitRepository(
        repository=repository,
        path_catalog=PathCatalog(paths=()),
        author_name="RememberStack",
        author_email="rememberstack@example.test",
    )
    checkout = tmp_path / "checkout"
    before = adapter.checkout(destination=checkout)
    (checkout / "K1/new.md").write_text("new compiled page\n", encoding="utf-8")

    after = adapter.publish(worktree=checkout)

    assert before != after
    assert (repository / "K1/new.md").read_text(encoding="utf-8") == (
        "new compiled page\n"
    )


def test_local_git_purge_rejects_traversing_catalog_paths(tmp_path: Path) -> None:
    """Never hand an untrusted escaping path to Git's index-filter shell."""
    repository = _repository(root=tmp_path)
    adapter = LocalGitRepository(
        repository=repository,
        path_catalog=PathCatalog(paths=("../outside.md",)),
        author_name="RememberStack",
        author_email="rememberstack@example.test",
    )

    with pytest.raises(ValueError):
        adapter.purge_artifacts(
            deployment_id=_DEPLOYMENT_ID,
            forget_id=_FORGET_ID,
            artifact_ids=(_ARTIFACT_ID,),
        )


def _commit(*, repository: Path, message: str) -> None:
    """Commit every current fixture change."""
    _git("-C", str(repository), "add", "-A")
    _git("-C", str(repository), "commit", "--quiet", "-m", message)


def _git(*arguments: str) -> None:
    """Run one fixture Git command and expose its complete failure."""
    subprocess.run(("git", *arguments), check=True, capture_output=True, text=True)


def _output(*arguments: str) -> str:
    """Run one fixture Git read and return trimmed stdout."""
    return subprocess.run(
        ("git", *arguments), check=True, capture_output=True, text=True
    ).stdout.strip()
