"""Real-Git proofs for the self-host K repository: snapshot, history erasure (D159)."""

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
from pathlib import Path
import subprocess
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LocalGitRepository
from rememberstack.adapters.selfhost.git import KRepositoryMovedError
from rememberstack.adapters.selfhost.git import SNAPSHOT_REF
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import KGitRemotePort

_TOKEN = "59100000-0000-0000-0000-00000000aaaa"
_ARTIFACT = UUID("59100000-0000-0000-0000-00000000bbbb")
_COMPILED = {"K1/compiled.md": _ARTIFACT}


class _Lease:
    """Count how often the K lease is held, and refuse to be nested."""

    def __init__(self) -> None:
        self.entered = 0

    @contextmanager
    def hold(self) -> Iterator[None]:
        self.entered += 1
        yield


def _ignore(ids: frozenset[UUID]) -> None:
    """No compiled page names the document outside the drop set."""
    assert not ids


def _none(ids: frozenset[UUID]) -> dict[UUID, str | None]:
    """No page has an accepted rebuild."""
    return dict.fromkeys(ids)


def _repository(*, root: Path) -> Path:
    """Renamed history citing the token, a clean current page and a control."""
    repository = root / "knowledge"
    _git("init", "--quiet", "-b", "main", str(repository))
    _git("-C", str(repository), "config", "user.name", "Fixture")
    _git("-C", str(repository), "config", "user.email", "fixture@example.test")
    legacy = repository / "K1/legacy.md"
    affected = repository / "K1/affected.md"
    unrelated = repository / "K1/unrelated.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(f"cites {_TOKEN} v1\n", encoding="utf-8")
    unrelated.write_text("unrelated v1\n", encoding="utf-8")
    (repository / "K1/:colon.md").write_text("a legal name\n", encoding="utf-8")
    _commit(repository=repository, message="initial")
    legacy.write_text(f"cites {_TOKEN.upper()} v2\n", encoding="utf-8")
    unrelated.write_text("unrelated v2\n", encoding="utf-8")
    _commit(repository=repository, message="update")
    _git("-C", str(repository), "mv", "K1/legacy.md", "K1/affected.md")
    _commit(repository=repository, message="rename affected page")
    affected.write_text("redacted current page\n", encoding="utf-8")
    _commit(repository=repository, message="owner redacts")
    _git("-C", str(repository), "tag", "-a", "v1", "-m", "release", "HEAD~1")
    # An ordinary clone's remote-tracking refs, including the symbolic HEAD.
    _git("-C", str(repository), "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(
        "-C",
        str(repository),
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
    )
    return repository


def _adapter(repository: Path) -> LocalGitRepository:
    return LocalGitRepository(
        repository=repository,
        author_name="RememberStack",
        author_email="rememberstack@example.test",
    )


def test_erase_rewrites_only_history_that_names_the_document(tmp_path: Path) -> None:
    """Every ref loses the token; current files and unrelated history stay."""
    repository = _repository(root=tmp_path)
    purge: KGitPurgePort = _adapter(repository)
    lease = _Lease()
    (repository / "K1/unrelated.md").write_text("uncommitted edit\n", encoding="utf-8")
    # A pre-D159 forget receipt pins the old branch, citing history included.
    _git("-C", str(repository), "update-ref", "refs/rememberstack/forget/old", "HEAD~2")

    first = purge.erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        dropped=_ignore,
        compiled=_COMPILED,
        drop=frozenset(),
        accepted=_none,
        lease=lease.hold(),
    )
    second = purge.erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        dropped=_ignore,
        compiled=_COMPILED,
        drop=frozenset(),
        accepted=_none,
        lease=lease.hold(),
    )

    assert (first.removed, first.blocking_paths) == (True, ())
    assert (second.removed, second.blocking_paths) == (False, ())
    assert lease.entered == 2  # every pass verifies and collects garbage
    assert _everything(repository).count(_TOKEN.encode()) == 0
    assert _everything(repository).lower().count(_TOKEN.encode()) == 0
    assert (repository / "K1/affected.md").read_text(encoding="utf-8") == (
        "redacted current page\n"
    )
    assert not (repository / "K1/legacy.md").exists()
    # An owner's uncommitted edit elsewhere survives; the tree is otherwise clean.
    assert (repository / "K1/unrelated.md").read_text(encoding="utf-8") == (
        "uncommitted edit\n"
    )
    assert _output("-C", str(repository), "status", "--porcelain") == (
        "M K1/unrelated.md"
    )
    assert _log(repository, "K1/unrelated.md") == 2
    assert _output("-C", str(repository), "rev-list", "--count", "HEAD") == "4"
    assert _output("-C", str(repository), "show", "v1:K1/affected.md") == (
        "redacted current page"
    )
    assert _output("-C", str(repository), "for-each-ref", "refs/original/") == ""
    assert _output("-C", str(repository), "for-each-ref", "refs/rememberstack/") == ""


def test_a_current_citation_blocks_and_keeps_its_history(tmp_path: Path) -> None:
    """The engine never rewrites an authored file that still names the document.

    Its old name is not a current file, so that history is still rewritten.
    """
    repository = _repository(root=tmp_path)
    (repository / "K1/affected.md").write_text(f"still {_TOKEN}\n", encoding="utf-8")
    _commit(repository=repository, message="cites again")
    lease = _Lease()

    first = _adapter(repository).erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        dropped=_ignore,
        compiled=_COMPILED,
        drop=frozenset(),
        accepted=_none,
        lease=lease.hold(),
    )
    second = _adapter(repository).erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        dropped=_ignore,
        compiled=_COMPILED,
        drop=frozenset(),
        accepted=_none,
        lease=lease.hold(),
    )

    assert (first.removed, first.blocking_paths) == (True, ("K1/affected.md",))
    assert (second.removed, second.blocking_paths) == (False, ("K1/affected.md",))
    assert lease.entered == 2  # every pass verifies and collects garbage
    assert _log(repository, "K1/legacy.md") == 0
    assert _log(repository, "K1/affected.md") == 3
    assert _output(
        "-C", str(repository), "log", "--all", "-S", _TOKEN, "--format=%s"
    ).splitlines() == ["cites again"]


def test_compiled_pages_form_a_parentless_snapshot_and_drop_without_trace(
    tmp_path: Path,
) -> None:
    """Publish keeps compiled pages off the branch; erase drops them for good."""
    repository = _repository(root=tmp_path)
    adapter: KGitRemotePort = _adapter(repository)
    checkout = tmp_path / "checkout"
    before = adapter.checkout(destination=checkout)
    (checkout / "K1/compiled.md").write_text(f"compiled {_TOKEN}\n", encoding="utf-8")
    (checkout / "K1/new.md").write_text("new authored page\n", encoding="utf-8")

    after = adapter.publish(worktree=checkout, compiled=_COMPILED)

    assert before != after
    assert (repository / "K1/new.md").read_text(encoding="utf-8") == (
        "new authored page\n"
    )
    assert not (repository / "K1/compiled.md").exists()
    assert _output("-C", str(repository), "rev-list", "--count", SNAPSHOT_REF) == "1"
    assert _log(repository, "K1/compiled.md", refs=("--branches",)) == 0
    reader = tmp_path / "reader"
    adapter.checkout(destination=reader)
    assert (reader / "K1/compiled.md").read_text(encoding="utf-8") == (
        f"compiled {_TOKEN}\n"
    )

    erasure = _adapter(repository).erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        dropped=_ignore,
        compiled=_COMPILED,
        drop=frozenset({_ARTIFACT}),
        accepted=_none,
        lease=_Lease().hold(),
    )

    assert erasure.removed
    assert SNAPSHOT_REF not in _output(
        "-C", str(repository), "for-each-ref", "--format=%(refname)"
    )
    assert _everything(repository).lower().count(_TOKEN.encode()) == 0


def test_a_compiled_page_committed_on_the_branch_wins_at_checkout(
    tmp_path: Path,
) -> None:
    """A direct edit reaches the driver, which quarantines it; nothing is lost."""
    repository = _repository(root=tmp_path)
    adapter = _adapter(repository)
    first = tmp_path / "first"
    adapter.checkout(destination=first)
    (first / "K1/compiled.md").write_text("machine text\n", encoding="utf-8")
    adapter.publish(worktree=first, compiled=_COMPILED)
    (repository / "K1/compiled.md").write_text("human edit\n", encoding="utf-8")
    _commit(repository=repository, message="direct edit")

    second = tmp_path / "second"
    adapter.checkout(destination=second)

    assert (second / "K1/compiled.md").read_text(encoding="utf-8") == "human edit\n"


def test_publish_refuses_a_moved_branch_and_keeps_the_human_commit(
    tmp_path: Path,
) -> None:
    """Publication is a compare-and-swap; it never discards a commit."""
    repository = _repository(root=tmp_path)
    adapter = _adapter(repository)
    checkout = tmp_path / "checkout"
    adapter.checkout(destination=checkout)
    (checkout / "K1/new.md").write_text("driver page\n", encoding="utf-8")
    (repository / "K1/human.md").write_text("human page\n", encoding="utf-8")
    _commit(repository=repository, message="human")

    with pytest.raises(KRepositoryMovedError):
        adapter.publish(worktree=checkout, compiled=_COMPILED)

    assert _output("-C", str(repository), "log", "-1", "--format=%s") == "human"
    assert _output("-C", str(repository), "for-each-ref", "refs/rememberstack/") == ""


def test_snapshot_only_publish_refuses_a_moved_branch(tmp_path: Path) -> None:
    """The branch a publication read is verified even when only pages change."""
    repository = _repository(root=tmp_path)
    adapter = _adapter(repository)
    checkout = tmp_path / "checkout"
    adapter.checkout(destination=checkout)
    (checkout / "K1/compiled.md").write_text("machine text\n", encoding="utf-8")
    (repository / "K1/compiled.md.curation.md").write_text("owner\n", encoding="utf-8")
    _commit(repository=repository, message="owner curates")

    with pytest.raises(KRepositoryMovedError):
        adapter.publish(worktree=checkout, compiled=_COMPILED)

    assert _output("-C", str(repository), "for-each-ref", "refs/rememberstack/") == ""


def test_an_accepted_rebuild_stays_and_replaced_snapshots_leave_no_object(
    tmp_path: Path,
) -> None:
    """Only a pending body is dropped; any pass collects unreachable objects."""
    repository = _repository(root=tmp_path)
    adapter = _adapter(repository)
    for body in (f"old {_TOKEN}\n", "clean rebuild\n"):
        checkout = tmp_path / f"checkout-{len(body)}"
        adapter.checkout(destination=checkout)
        (checkout / "K1/compiled.md").write_text(body, encoding="utf-8")
        adapter.publish(worktree=checkout, compiled=_COMPILED)
    rebuilt = hashlib.sha256(b"clean rebuild\n").hexdigest()

    erasure = adapter.erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        dropped=_ignore,
        compiled=_COMPILED,
        drop=frozenset({_ARTIFACT}),
        accepted=lambda ids: dict.fromkeys(ids, rebuilt),
        lease=_Lease().hold(),
    )

    assert (erasure.removed, erasure.blocking_paths) == (True, ())  # old history
    assert _output("-C", str(repository), "show", f"{SNAPSHOT_REF}:K1/compiled.md") == (
        "clean rebuild"
    )
    assert _everything(repository).lower().count(_TOKEN.encode()) == 0


def test_the_pre_d159_branch_migrates_in_one_publication(tmp_path: Path) -> None:
    """Machine-written page revisions leave history; a person's revision stays."""
    repository = _repository(root=tmp_path)
    page = repository / "K1/compiled.md"
    for body in ("machine v1\n", "machine v2\n"):
        page.write_text(body, encoding="utf-8")
        _git("-C", str(repository), "add", "-A")
        _git(
            "-C",
            str(repository),
            "-c",
            "user.name=RememberStack",
            "-c",
            "user.email=rememberstack@example.test",
            "commit",
            "--quiet",
            "-m",
            "rememberstack knowledge update",
        )
    page.write_text("a person's edit\n", encoding="utf-8")
    _commit(repository=repository, message="direct edit")
    adapter = _adapter(repository)
    checkout = tmp_path / "checkout"
    adapter.checkout(destination=checkout)

    revision = adapter.publish(worktree=checkout, compiled=_COMPILED)

    assert revision.root == _output("-C", str(repository), "rev-parse", "HEAD")
    bodies = _output(
        "-C", str(repository), "log", "--branches", "-p", "--", "K1/compiled.md"
    )
    assert "machine v" not in bodies
    assert "a person's edit" in bodies
    assert not page.exists()
    assert _output("-C", str(repository), "show", f"{SNAPSHOT_REF}:K1/compiled.md") == (
        "a person's edit"
    )
    assert b"machine v" not in _everything(repository)
    assert _output("-C", str(repository), "status", "--porcelain") == ""


def test_a_stale_migrating_publish_cannot_restore_a_dropped_page(
    tmp_path: Path,
) -> None:
    """The migration verifies the snapshot its cycle checked out."""
    repository = _repository(root=tmp_path)
    adapter = _adapter(repository)
    other = UUID("59100000-0000-0000-0000-00000000cccc")
    compiled = {**_COMPILED, "K1/other.md": other}
    seed = tmp_path / "seed"
    adapter.checkout(destination=seed)
    (seed / "K1/other.md").write_text(f"names {_TOKEN}\n", encoding="utf-8")
    adapter.publish(worktree=seed, compiled=compiled)
    # A person commits a compiled path on the branch: the next publish migrates.
    (repository / "K1/compiled.md").write_text("direct edit\n", encoding="utf-8")
    _commit(repository=repository, message="direct edit")
    stale = tmp_path / "stale"
    adapter.checkout(destination=stale)
    adapter.erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        compiled=compiled,
        drop=frozenset({other}),
        accepted=_none,
        dropped=_ignore,
        lease=_Lease().hold(),
    )

    with pytest.raises(KRepositoryMovedError):
        adapter.publish(worktree=stale, compiled=compiled)

    # The refused publish fetched its objects; the next pass collects them.
    again = adapter.erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        compiled=compiled,
        drop=frozenset({other}),
        accepted=_none,
        dropped=_ignore,
        lease=_Lease().hold(),
    )
    assert not again.removed
    assert _everything(repository).lower().count(_TOKEN.encode()) == 0


def test_a_page_whose_band_names_an_erased_id_leaves_the_snapshot(
    tmp_path: Path,
) -> None:
    """A generated fact band can name an erased fact without a citation."""
    repository = _repository(root=tmp_path)
    adapter = _adapter(repository)
    checkout = tmp_path / "checkout"
    adapter.checkout(destination=checkout)
    (checkout / "K1/compiled.md").write_text(
        f"prose\n\n## Fact sheet\n- relation {_TOKEN}\n", encoding="utf-8"
    )
    adapter.publish(worktree=checkout, compiled=_COMPILED)
    reported: list[frozenset[UUID]] = []

    erasure = adapter.erase(
        needles=(_TOKEN,),
        page_needles=(_TOKEN,),
        compiled=_COMPILED,
        drop=frozenset(),
        accepted=_none,
        dropped=reported.append,
        lease=_Lease().hold(),
    )

    assert erasure.removed
    assert reported == [frozenset({_ARTIFACT})]
    assert _everything(repository).lower().count(_TOKEN.encode()) == 0


def test_erase_rejects_traversing_paths(tmp_path: Path) -> None:
    """Never hand an escaping path to Git or the filesystem."""
    repository = _repository(root=tmp_path)

    with pytest.raises(ValueError):
        _adapter(repository).erase(
            needles=(_TOKEN,),
            page_needles=(_TOKEN,),
            dropped=_ignore,
            compiled={"../outside.md": _ARTIFACT},
            drop=frozenset(),
            accepted=_none,
            lease=_Lease().hold(),
        )


def _everything(repository: Path) -> bytes:
    """Every object the repository still stores, reachable or not."""
    return subprocess.run(
        ("git", "-C", str(repository), "cat-file", "--batch-all-objects", "--batch"),
        check=True,
        capture_output=True,
    ).stdout


def _log(repository: Path, path: str, refs: tuple[str, ...] = ("--all",)) -> int:
    """How many commits on ``refs`` touch ``path``."""
    return len(
        _output(
            "-C", str(repository), "log", *refs, "--format=%H", "--", path
        ).splitlines()
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
