"""Self-host Plane-K truth: one local Git repository, with D159 erasure.

Authored pages, curation sidecars and every other human file live on the
checked-out branch with ordinary history. Compiled pages live in a replaceable
snapshot: one parentless commit at ``refs/rememberstack/compiled`` that each
publication replaces, so compiled text never accumulates Git history (D159
§3.3 step 4). The snapshot commit's message lists which artifact owns each
path. Every publication is one ref transaction that first verifies every ref
the writer read, made while the caller holds the K lease.
"""

from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Mapping
from contextlib import AbstractContextManager
import hashlib
import io
import json
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from uuid import UUID

from rememberstack.core import cites_forgotten
from rememberstack.model import KnowledgeErasure
from rememberstack.model import KRevision

SNAPSHOT_REF = "refs/rememberstack/compiled"
"""The compiled snapshot: one parentless commit, replaced by every publication."""

_BASE_REF = "refs/rememberstack/base"
"""In a driver checkout: the branch head it was cloned at."""

_OUT = "refs/rememberstack/out/"
_IN = "refs/rememberstack/in/"
_OWN_REFS = "refs/rememberstack/"
_BLOB_MODES = frozenset({"100644", "100755", "120000"})
_SNAPSHOT_MESSAGE = "rememberstack compiled snapshot"

_Entry = tuple[str, str]
"""A tree entry's mode and object ID."""

AcceptedHashes = Callable[[frozenset[UUID]], Mapping[UUID, str | None]]
"""Read under the K lease: each compiled page's accepted content hash."""


class KRepositoryMovedError(RuntimeError):
    """The repository changed since this writer read it; it publishes nothing."""


class LocalGitRepository:
    """Use one local working repository as self-host Plane-K truth."""

    def __init__(
        self, *, repository: Path, author_name: str, author_email: str
    ) -> None:
        """Bind an existing repository and the driver's commit identity.

        The identity also tells which revisions of a pre-D159 branch were
        machine-written compiled pages.
        """
        self._repository = repository.resolve(strict=True)
        self._author_name = author_name
        self._author_email = author_email
        if _git(self._repository, "rev-parse", "--is-bare-repository") == "true":
            raise ValueError("self-host Plane-K truth must be a working repository")

    def checkout(self, *, destination: Path) -> KRevision:
        """Clone the branch and add the compiled snapshot's files to it."""
        _run("git", "clone", "--quiet", str(self._repository), str(destination))
        head = _git(destination, "rev-parse", "HEAD")
        _git(destination, "update-ref", _BASE_REF, head)
        snapshot = _ref(self._repository, SNAPSHOT_REF)
        if snapshot is not None:
            _git(
                destination,
                "fetch",
                "--quiet",
                "--no-tags",
                str(self._repository),
                f"+{SNAPSHOT_REF}:{SNAPSHOT_REF}",
            )
            archive = subprocess.run(
                ("git", "-C", str(destination), "archive", "--format=tar", snapshot),
                check=True,
                capture_output=True,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as files:
                # A file committed on the branch wins: a direct edit of a
                # compiled page is then seen (and quarantined) by the driver.
                files.extractall(
                    destination,
                    members=[
                        member
                        for member in files.getmembers()
                        if member.isdir() or not (destination / member.name).exists()
                    ],
                    filter="data",
                )
        return KRevision(root=head)

    def publish(self, *, worktree: Path, compiled: Mapping[str, UUID]) -> KRevision:
        """Commit non-compiled files to the branch and replace the snapshot."""
        paths = _validated(worktree, compiled)
        base = _ref(worktree, _BASE_REF)
        if base is None:
            raise ValueError("publish needs a worktree made by checkout")
        branch = _git(worktree, "symbolic-ref", "HEAD")
        old_snapshot = _ref(worktree, SNAPSHOT_REF)
        legacy = bool(paths.intersection(_tree(worktree, base)))
        _git(worktree, "add", "-A")
        if paths:
            _git(
                worktree,
                "--literal-pathspecs",
                "rm",
                "-r",
                "--cached",
                "--quiet",
                "--ignore-unmatch",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                stdin="\0".join(sorted(paths)),
            )
        if _status(worktree, "diff", "--cached", "--quiet") != 0:
            _git(
                worktree,
                *self._identity(),
                "commit",
                "--quiet",
                "-m",
                "rememberstack knowledge update",
            )
        head = _git(worktree, "rev-parse", "HEAD")
        present = sorted(path for path in paths if (worktree / path).is_file())
        entries: dict[str, _Entry] = {}
        if present:
            hashes = _git(
                worktree, "hash-object", "-w", "--stdin-paths", stdin="\n".join(present)
            ).splitlines()
            entries = {
                path: ("100644", value)
                for path, value in zip(present, hashes, strict=True)
            }
        new_snapshot = self._snapshot(
            repository=worktree,
            old=old_snapshot,
            entries=entries,
            owners={path: compiled[path] for path in entries},
        )
        if legacy:
            self._migrate(
                worktree=worktree,
                branch=branch,
                base=base,
                head=head,
                snapshot=new_snapshot,
                compiled=paths,
            )
        else:
            self._advance(
                source=worktree,
                expected={branch: base, SNAPSHOT_REF: old_snapshot},
                updates={branch: head, SNAPSHOT_REF: new_snapshot},
            )
        return KRevision(root=head)

    def erase(
        self,
        *,
        needles: tuple[str, ...],
        compiled: Mapping[str, UUID],
        drop: frozenset[UUID],
        accepted: AcceptedHashes,
        lease: AbstractContextManager[None],
    ) -> KnowledgeErasure:
        """Rewrite in a private mirror, then publish it under ``lease``."""
        paths = _validated(self._repository, compiled)
        with TemporaryDirectory(prefix="rememberstack-k-erase-") as temporary:
            mirror = self._mirror(Path(temporary))
            branch = _git(mirror, "symbolic-ref", "HEAD")
            read = _history_refs(mirror, aliases=self._aliases())
            snapshot = _ref(mirror, SNAPSHOT_REF)
            blocking: tuple[str, ...] = ()
            legacy_tip: dict[str, _Entry] = {}
            if branch in read:
                blocking, legacy_tip = self._rewrite_history(
                    mirror=mirror,
                    refs=tuple(sorted(read)),
                    branch=branch,
                    needles=needles,
                    compiled=paths,
                )
            rewritten = _history_refs(mirror, aliases=self._aliases())
            with lease:
                hashes = accepted(drop) if drop else {}
                entries = {} if snapshot is None else _tree(mirror, snapshot)
                owners = {
                    **{path: compiled[path] for path in paths},
                    **({} if snapshot is None else _owners(mirror, snapshot)),
                }
                kept = {
                    path: entry
                    for path, entry in {**legacy_tip, **entries}.items()
                    if not self._dropped(
                        mirror=mirror,
                        entry=entry,
                        owner=owners.get(path),
                        drop=drop,
                        accepted=hashes,
                    )
                }
                new_snapshot = self._snapshot(
                    repository=mirror,
                    old=snapshot,
                    entries=kept,
                    owners={p: owners[p] for p in kept if p in owners},
                )
                updates: dict[str, str | None] = {
                    ref: value
                    for ref, value in rewritten.items()
                    if read.get(ref) != value
                }
                if new_snapshot != snapshot:
                    updates[SNAPSHOT_REF] = new_snapshot
                self._advance(
                    source=mirror,
                    expected={**read, SNAPSHOT_REF: snapshot},
                    updates=updates,
                )
                # Every pass collects garbage, so a pass that died after its
                # ref update still leaves no unreachable forgotten object.
                self._collect_garbage()
        return KnowledgeErasure(removed=bool(updates), blocking_paths=blocking)

    def _dropped(
        self,
        *,
        mirror: Path,
        entry: _Entry,
        owner: UUID | None,
        drop: frozenset[UUID],
        accepted: Mapping[UUID, str | None],
    ) -> bool:
        """A pending page leaves the snapshot unless it is an accepted rebuild."""
        if owner is None or owner not in drop:
            return False
        body = subprocess.run(
            ("git", "-C", str(mirror), "cat-file", "blob", entry[1]),
            check=True,
            capture_output=True,
        ).stdout
        return hashlib.sha256(body).hexdigest() != accepted.get(owner)

    def _migrate(
        self,
        *,
        worktree: Path,
        branch: str,
        base: str,
        head: str,
        snapshot: str | None,
        compiled: frozenset[str],
    ) -> None:
        """One-time cleanup of a pre-D159 branch, published with the snapshot.

        The branch carried compiled pages: their machine-written revisions
        leave the branch history in the same publication that moves the pages
        into the snapshot. Revisions a person wrote at those paths stay.
        """
        with TemporaryDirectory(prefix="rememberstack-k-migrate-") as temporary:
            mirror = self._mirror(Path(temporary))
            read = _history_refs(mirror, aliases=self._aliases())
            read_snapshot = _ref(mirror, SNAPSHOT_REF)
            outgoing = {f"{_OUT}head": head}
            if snapshot is not None:
                outgoing[f"{_OUT}snapshot"] = snapshot
            for ref, value in outgoing.items():
                _git(worktree, "update-ref", ref, value)
            try:
                _git(
                    mirror,
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--update-head-ok",
                    str(worktree),
                    f"+{_OUT}head:{branch}",
                    *((f"+{_OUT}snapshot:{SNAPSHOT_REF}",) if snapshot else ()),
                )
            finally:
                for ref in outgoing:
                    _git(worktree, "update-ref", "-d", ref)
            if snapshot is None and read_snapshot is not None:
                _git(mirror, "update-ref", "-d", SNAPSHOT_REF)
            self._rewrite_history(
                mirror=mirror,
                refs=tuple(sorted(_history_refs(mirror, aliases=self._aliases()))),
                branch=branch,
                needles=(),
                compiled=compiled,
            )
            rewritten = _history_refs(mirror, aliases=self._aliases())
            updates: dict[str, str | None] = {
                ref: value for ref, value in rewritten.items() if read.get(ref) != value
            }
            if snapshot != read_snapshot:
                updates[SNAPSHOT_REF] = snapshot
            self._advance(
                source=mirror,
                expected={**read, branch: base, SNAPSHOT_REF: read_snapshot},
                updates=updates,
            )
            self._collect_garbage()

    def _rewrite_history(
        self,
        *,
        mirror: Path,
        refs: tuple[str, ...],
        branch: str,
        needles: tuple[str, ...],
        compiled: frozenset[str],
    ) -> tuple[tuple[str, ...], dict[str, _Entry]]:
        """Rewrite ``refs`` in the mirror; return blocking paths and legacy pages.

        Erased: every revision of a path whose bytes name a needle, unless the
        path's current file on ``branch`` still does (it blocks until its
        owner edits it), and every machine-written revision of a compiled path
        (pre-D159 layout). An erased revision becomes the path's current file
        when that is clean, or disappears.
        """
        tip = _tree(mirror, _git(mirror, "rev-parse", branch))
        pairs = _history_entries(mirror, refs)
        machine = _history_entries(mirror, refs, author=self._author_email)
        legacy = {(path, value) for path, _, value in machine if path in compiled}
        citing = _citing_blobs(
            mirror,
            {value for _, mode, value in pairs if mode in _BLOB_MODES}
            | {value for _, value in tip.values()},
            needles,
        )
        blocking = tuple(
            sorted(
                path
                for path, (_, value) in tip.items()
                if path not in compiled and value in citing
            )
        )
        legacy_tip = {
            path: entry
            for path, entry in tip.items()
            if (path, entry[1]) in legacy and entry[1] not in citing
        }
        if any(
            (path, value) in legacy or (value in citing and path not in blocking)
            for path, _, value in pairs
        ):
            plan_file = mirror.parent / "plan.json"
            plan_file.write_text(
                json.dumps(
                    {
                        "legacy": sorted([path, value] for path, value in legacy),
                        "citing": sorted(citing),
                        "blocking": list(blocking),
                        "tip": {path: list(entry) for path, entry in tip.items()},
                    }
                ),
                encoding="utf-8",
            )
            script = mirror.parent / "index_filter.py"
            script.write_text(_INDEX_FILTER, encoding="utf-8")
            command = " ".join(
                shlex.quote(part)
                for part in (sys.executable, "-I", str(script), str(plan_file))
            )
            _git(
                mirror,
                "filter-branch",
                "--force",
                "--index-filter",
                command,
                "--tag-name-filter",
                "cat",
                "--",
                *refs,
                env={"FILTER_BRANCH_SQUELCH_WARNING": "1"},
            )
            for ref in _refs(mirror, "refs/original/"):
                _git(mirror, "update-ref", "-d", ref)
        return blocking, legacy_tip

    def _snapshot(
        self,
        *,
        repository: Path,
        old: str | None,
        entries: Mapping[str, _Entry],
        owners: Mapping[str, UUID],
    ) -> str | None:
        """The snapshot commit for ``entries``: the old one if unchanged."""
        if not entries:
            return None
        with TemporaryDirectory(prefix="rememberstack-k-index-") as temporary:
            index = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
            _git(
                repository,
                "update-index",
                "-z",
                "--index-info",
                stdin="".join(
                    f"{mode} {value}\t{path}\0"
                    for path, (mode, value) in sorted(entries.items())
                ),
                env=index,
            )
            tree = _git(repository, "write-tree", env=index)
        if (
            old is not None
            and _git(repository, "rev-parse", f"{old}^{{tree}}") == tree
            and _owners(repository, old) == owners
        ):
            return old
        message = "\n".join(
            (
                _SNAPSHOT_MESSAGE,
                "",
                *(f"{owner} {path}" for path, owner in sorted(owners.items())),
            )
        )
        return _git(repository, *self._identity(), "commit-tree", tree, "-m", message)

    def _advance(
        self,
        *,
        source: Path,
        expected: Mapping[str, str | None],
        updates: Mapping[str, str | None],
    ) -> None:
        """Move truth's refs from what the writer read to what it prepared.

        One ref transaction verifies every ref in ``expected`` and is prepared
        (its refs locked) before the checked-out branch's files change; those
        follow with a two-tree merge, which keeps unrelated uncommitted edits
        and refuses ones the update would overwrite.
        """
        truth = self._repository
        changes = {ref: new for ref, new in updates.items() if new != expected.get(ref)}
        outgoing = {
            f"{_OUT}{number}": new
            for number, new in enumerate(changes.values())
            if new is not None
        }
        for ref, value in outgoing.items():
            _git(source, "update-ref", ref, value)
        try:
            if outgoing:
                _git(
                    truth,
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    str(source),
                    *(f"+{ref}:{_IN}{ref.removeprefix(_OUT)}" for ref in outgoing),
                )
            commands = [
                _transaction_line(ref=ref, old=expected.get(ref), new=changes[ref])
                if ref in changes
                else f"verify {ref} {expected[ref] or ''}".rstrip()
                for ref in sorted({*expected, *changes})
            ]
            checked_out = _git(truth, "symbolic-ref", "-q", "HEAD", check=False)
            old, new = expected.get(checked_out), changes.get(checked_out)
            move_files = (
                checked_out in changes
                and old is not None
                and new is not None
                and _git(truth, "rev-parse", f"{old}^{{tree}}")
                != _git(truth, "rev-parse", f"{new}^{{tree}}")
            )
            _transact(
                repository=truth,
                commands=commands,
                before_commit=(
                    (lambda: _git(truth, "read-tree", "-m", "-u", str(old), str(new)))
                    if move_files
                    else None
                ),
            )
        finally:
            for ref in _refs(truth, _IN):
                _git(truth, "update-ref", "-d", ref)
            for ref in outgoing:
                _git(source, "update-ref", "-d", ref)

    def _collect_garbage(self) -> None:
        """Remove unreachable objects (replaced snapshots, rewritten commits) now."""
        for ref in _refs(self._repository, _IN):
            _git(self._repository, "update-ref", "-d", ref)
        _git(self._repository, "reflog", "expire", "--expire=now", "--all")
        _git(self._repository, "gc", "--quiet", "--prune=now")

    def _mirror(self, directory: Path) -> Path:
        """A private bare copy of every ref of truth."""
        mirror = directory / "mirror.git"
        _run("git", "clone", "--quiet", "--mirror", str(self._repository), str(mirror))
        return mirror

    def _aliases(self) -> frozenset[str]:
        """Truth's symbolic refs, such as ``refs/remotes/origin/HEAD``."""
        output = _git(self._repository, "for-each-ref", "--format=%(symref) %(refname)")
        return frozenset(
            name
            for symbolic, _, name in (
                line.partition(" ") for line in output.splitlines()
            )
            if symbolic
        )

    def _identity(self) -> tuple[str, ...]:
        return (
            "-c",
            f"user.name={self._author_name}",
            "-c",
            f"user.email={self._author_email}",
        )


_INDEX_FILTER = """
import json
import subprocess
import sys

plan = json.loads(open(sys.argv[1], encoding="utf-8").read())
legacy = {tuple(pair) for pair in plan["legacy"]}
citing = set(plan["citing"])
blocking = set(plan["blocking"])
tip = plan["tip"]
listing = subprocess.run(
    ["git", "ls-files", "-s", "-z"], check=True, capture_output=True
).stdout
changes = []
for record in listing.split(b"\\0"):
    if not record:
        continue
    meta, raw_path = record.split(b"\\t", 1)
    mode, value, _ = meta.decode("ascii").split()
    path = raw_path.decode("utf-8", "surrogateescape")
    if (path, value) in legacy or (value in citing and path not in blocking):
        current = tip.get(path)
        if (
            current is not None
            and current[1] not in citing
            and (path, current[1]) not in legacy
        ):
            changes.append(f"{current[0]} {current[1]}\\t{path}")
        else:
            changes.append(f"0 {'0' * len(value)}\\t{path}")
if changes:
    subprocess.run(
        ["git", "update-index", "-z", "--index-info"],
        input="".join(change + "\\0" for change in changes).encode(
            "utf-8", "surrogateescape"
        ),
        check=True,
    )
"""
"""The index filter: erase machine-written legacy pages and needle-naming files."""


def _transaction_line(*, ref: str, old: str | None, new: str | None) -> str:
    if new is None:
        return f"delete {ref} {old}"
    if old is None:
        return f"create {ref} {new}"
    return f"update {ref} {new} {old}"


def _transact(
    *, repository: Path, commands: list[str], before_commit: Callable[[], object] | None
) -> None:
    """Run one ``update-ref --stdin`` transaction: prepare, act, then commit."""
    process = subprocess.Popen(
        ("git", "-C", str(repository), "update-ref", "--stdin"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None

    def send(line: str, answer: str | None = None) -> None:
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(line + "\n")
            process.stdin.flush()
        except BrokenPipeError:
            _, error = process.communicate()
            raise KRepositoryMovedError(
                f"K repository changed: {error.strip()}"
            ) from None
        if answer is not None and process.stdout.readline().strip() != answer:
            _, error = process.communicate()
            raise KRepositoryMovedError(f"K repository changed: {error.strip()}")

    try:
        send("start", "start: ok")
        for command in commands:
            send(command)
        send("prepare", "prepare: ok")
        if before_commit is not None:
            try:
                before_commit()
            except BaseException:
                send("abort", "abort: ok")
                raise
        send("commit", "commit: ok")
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait()


def _history_entries(
    repository: Path, refs: tuple[str, ...], author: str | None = None
) -> set[tuple[str, str, str]]:
    """Every (path, mode, object) that a commit of ``refs`` introduced.

    ``author``, an email, keeps only the commits it wrote.
    """
    if not refs:
        return set()
    output = subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "log",
            "--no-renames",
            "--root",
            "-m",
            "--raw",
            "--no-abbrev",
            "--format=",
            "-z",
            *(("--fixed-strings", f"--author=<{author}>") if author else ()),
            *refs,
            "--",
        ),
        check=True,
        capture_output=True,
    ).stdout
    tokens = output.split(b"\0")
    entries: set[tuple[str, str, str]] = set()
    index = 0
    while index < len(tokens):
        meta = tokens[index].strip(b"\n")
        if not meta.startswith(b":"):
            index += 1
            continue
        # A header is always followed by exactly one path (no renames).
        _, mode, _, value, status = meta[1:].decode("ascii").split()
        if status != "D" and index + 1 < len(tokens):
            path = tokens[index + 1].decode("utf-8", "surrogateescape")
            entries.add((path, mode, value))
        index += 2
    return entries


def _citing_blobs(
    repository: Path, blobs: set[str], needles: tuple[str, ...]
) -> frozenset[str]:
    """The blobs whose bytes name a needle."""
    if not blobs or not needles:
        return frozenset()
    ordered = sorted(blobs)
    output = subprocess.run(
        ("git", "-C", str(repository), "cat-file", "--batch"),
        input="".join(f"{value}\n" for value in ordered).encode("ascii"),
        check=True,
        capture_output=True,
    ).stdout
    citing: set[str] = set()
    position = 0
    for value in ordered:
        header_end = output.index(b"\n", position)
        size = int(output[position:header_end].split()[2])
        content = output[header_end + 1 : header_end + 1 + size]
        position = header_end + 1 + size + 1
        if cites_forgotten(content=content, needles=needles):
            citing.add(value)
    return frozenset(citing)


def _tree(repository: Path, revision: str) -> dict[str, _Entry]:
    """Every blob entry of a commit's tree, by path."""
    output = subprocess.run(
        ("git", "-C", str(repository), "ls-tree", "-r", "-z", revision),
        check=True,
        capture_output=True,
    ).stdout
    entries: dict[str, _Entry] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, value = meta.decode("ascii").split()
        if kind == "blob":
            entries[raw_path.decode("utf-8", "surrogateescape")] = (mode, value)
    return entries


def _owners(repository: Path, snapshot: str) -> dict[str, UUID]:
    """The artifact that owns each path, from the snapshot commit's message."""
    message = _git(repository, "log", "-1", "--format=%B", snapshot)
    owners: dict[str, UUID] = {}
    for line in message.splitlines()[2:]:
        owner, _, path = line.partition(" ")
        try:
            owners[path] = UUID(owner)
        except ValueError:
            continue
    return owners


def _history_refs(
    repository: Path, aliases: frozenset[str] = frozenset()
) -> dict[str, str]:
    """Every ref that carries history, except symbolic ones and our own.

    ``aliases`` names refs that are symbolic in truth (a mirror clone stores
    them as plain refs); they follow their target and are never written.
    """
    output = _git(
        repository, "for-each-ref", "--format=%(objectname) %(symref) %(refname)"
    )
    refs: dict[str, str] = {}
    for line in output.splitlines():
        value, symbolic, name = line.split(" ", 2)
        if not symbolic and name not in aliases and not name.startswith(_OWN_REFS):
            refs[name] = value
    return refs


def _refs(repository: Path, prefix: str) -> tuple[str, ...]:
    """The refs under ``prefix``."""
    return tuple(
        _git(repository, "for-each-ref", "--format=%(refname)", prefix).splitlines()
    )


def _ref(repository: Path, ref: str) -> str | None:
    """The object a ref names, or None when it does not exist."""
    value = _git(repository, "rev-parse", "--verify", "-q", ref, check=False)
    return value or None


def _validated(repository: Path, paths: Collection[str]) -> frozenset[str]:
    """Reject absolute, traversing or multi-line paths before Git or file use."""
    result: set[str] = set()
    for value in paths:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or "\n" in value
            or "\0" in value
        ):
            raise ValueError(f"unsafe Plane-K path {value!r}")
        if not (repository / path).resolve().is_relative_to(repository.resolve()):
            raise ValueError(f"Plane-K path {value!r} escapes the repository")
        result.add(path.as_posix())
    return frozenset(result)


def _git(
    repository: Path,
    *arguments: str,
    stdin: str | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> str:
    """Run one Git command in ``repository`` and return its trimmed stdout.

    ``env`` adds variables for this command only, through ``env(1)``.
    """
    prefix = ("env", *(f"{key}={value}" for key, value in (env or {}).items()))
    completed = subprocess.run(
        (*(prefix if env else ()), "git", "-C", str(repository), *arguments),
        input=stdin,
        check=check,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _status(repository: Path, *arguments: str) -> int:
    """Run a Git predicate whose nonzero status is an expected false result."""
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=False,
        capture_output=True,
        text=True,
    ).returncode


def _run(*arguments: str) -> None:
    """Run one Git command and preserve its full failing process exception."""
    subprocess.run(arguments, check=True, capture_output=True, text=True)
