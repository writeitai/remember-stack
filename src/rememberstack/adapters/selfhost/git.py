"""Self-host Plane-K truth: one local Git repository, with D159 erasure.

Authored pages, curation sidecars and every other human file live on the
checked-out branch with ordinary history. Compiled pages live in a replaceable
snapshot: one parentless commit at ``refs/rememberstack/compiled`` that each
publication replaces, so compiled text never accumulates Git history (D159
§3.3 step 4). Every ref update is a compare-and-swap against what the writer
read, made while the caller holds the K lease.
"""

from collections.abc import Collection
from collections.abc import Mapping
from contextlib import AbstractContextManager
from contextlib import nullcontext
import io
import json
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

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

_Entry = tuple[str, str]
"""A tree entry's mode and object ID."""

_Update = tuple[str, str | None, str | None]
"""A ref, the value the writer read (None: absent) and its new value."""


class KRepositoryMovedError(RuntimeError):
    """The repository changed since this writer read it; it publishes nothing."""


class LocalGitRepository:
    """Use one local working repository as self-host Plane-K truth."""

    def __init__(
        self, *, repository: Path, author_name: str, author_email: str
    ) -> None:
        """Bind an existing repository and explicit commit identity."""
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

    def publish(self, *, worktree: Path, compiled_paths: Collection[str]) -> KRevision:
        """Commit non-compiled files to the branch and replace the snapshot."""
        compiled = _validated(worktree, compiled_paths)
        base = _ref(worktree, _BASE_REF)
        if base is None:
            raise ValueError("publish needs a worktree made by checkout")
        branch = _git(worktree, "symbolic-ref", "HEAD")
        old_snapshot = _ref(worktree, SNAPSHOT_REF)
        legacy = compiled.intersection(_tree(worktree, base))
        _git(worktree, "add", "-A")
        if compiled:
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
                stdin="\0".join(sorted(compiled)),
            )
        if _status(worktree, "diff", "--cached", "--quiet") != 0:
            _git(
                worktree,
                "-c",
                f"user.name={self._author_name}",
                "-c",
                f"user.email={self._author_email}",
                "commit",
                "--quiet",
                "-m",
                "rememberstack knowledge update",
            )
        head = _git(worktree, "rev-parse", "HEAD")
        present = sorted(path for path in compiled if (worktree / path).is_file())
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
            repository=worktree, old=old_snapshot, entries=entries
        )
        updates: list[_Update] = []
        if head != base:
            updates.append((branch, base, head))
        if new_snapshot != old_snapshot:
            updates.append((SNAPSHOT_REF, old_snapshot, new_snapshot))
        self._advance(source=worktree, updates=updates, prune=False)
        if legacy:
            # One-time cleanup: the branch carried compiled pages before D159.
            self.erase(
                needles=(), compiled_paths=compiled, drop_paths=(), lease=nullcontext()
            )
        return KRevision(root=head)

    def erase(
        self,
        *,
        needles: tuple[str, ...],
        compiled_paths: frozenset[str],
        drop_paths: tuple[str, ...],
        lease: AbstractContextManager[None],
    ) -> KnowledgeErasure:
        """Rewrite in a private mirror, then publish it under ``lease``."""
        compiled = _validated(self._repository, compiled_paths)
        drop = _validated(self._repository, drop_paths)
        with TemporaryDirectory(prefix="rememberstack-k-erase-") as temporary:
            mirror = Path(temporary) / "mirror.git"
            _run(
                "git",
                "clone",
                "--quiet",
                "--mirror",
                str(self._repository),
                str(mirror),
            )
            refs = _refs(mirror)
            branch = _git(mirror, "symbolic-ref", "HEAD")
            head = refs.get(branch)
            if head is None:
                return KnowledgeErasure(removed=False)
            history = tuple(
                sorted(ref for ref in refs if not ref.startswith(_OWN_REFS))
            )
            tip = _tree(mirror, head)
            snapshot = refs.get(SNAPSHOT_REF)
            snapshot_entries = {} if snapshot is None else _tree(mirror, snapshot)
            pairs = _history_entries(mirror, history)
            citing = _citing_blobs(
                mirror,
                {value for _, mode, value in pairs if mode in _BLOB_MODES},
                needles,
            )
            blocking = tuple(
                sorted(
                    path
                    for path, (_, value) in tip.items()
                    if path not in compiled and value in citing
                )
            )
            rewrite = any(
                path in compiled or (value in citing and path not in blocking)
                for path, _, value in pairs
            )
            new_entries = dict(snapshot_entries)
            for path, entry in tip.items():
                # A pre-D159 branch still carries compiled pages: keep them as
                # the snapshot unless they name the document.
                if path in compiled and entry[1] not in citing:
                    new_entries.setdefault(path, entry)
            for path in drop:
                new_entries.pop(path, None)
            if not rewrite and new_entries == snapshot_entries:
                return KnowledgeErasure(removed=False, blocking_paths=blocking)
            updates: list[_Update] = []
            if rewrite:
                self._rewrite(
                    mirror=mirror,
                    refs=history,
                    plan={
                        "compiled": sorted(compiled),
                        "citing": sorted(citing),
                        "blocking": list(blocking),
                        "tip": {path: list(entry) for path, entry in tip.items()},
                    },
                )
                rewritten = _refs(mirror)
                updates.extend(
                    (ref, refs[ref], rewritten.get(ref))
                    for ref in history
                    if rewritten.get(ref) != refs[ref]
                )
            new_snapshot = self._snapshot(
                repository=mirror, old=snapshot, entries=new_entries
            )
            if new_snapshot != snapshot:
                updates.append((SNAPSHOT_REF, snapshot, new_snapshot))
            with lease:
                self._advance(source=mirror, updates=updates, prune=True)
        return KnowledgeErasure(removed=True, blocking_paths=blocking)

    def _snapshot(
        self, *, repository: Path, old: str | None, entries: Mapping[str, _Entry]
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
                stdin="\0".join(
                    f"{mode} {value}\t{path}"
                    for path, (mode, value) in sorted(entries.items())
                )
                + "\0",
                env=index,
            )
            tree = _git(repository, "write-tree", env=index)
        if old is not None and _git(repository, "rev-parse", f"{old}^{{tree}}") == tree:
            return old
        return _git(
            repository,
            "-c",
            f"user.name={self._author_name}",
            "-c",
            f"user.email={self._author_email}",
            "commit-tree",
            tree,
            "-m",
            "rememberstack compiled snapshot",
        )

    def _rewrite(
        self, *, mirror: Path, refs: tuple[str, ...], plan: Mapping[str, object]
    ) -> None:
        """Run the index filter over every history ref of the private mirror."""
        plan_file = mirror.parent / "plan.json"
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
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

    def _advance(self, *, source: Path, updates: list[_Update], prune: bool) -> None:
        """Move truth's refs from what the writer read to what it prepared.

        Each ref must still hold its expected value. The checked-out branch's
        files follow with a two-tree merge, which keeps unrelated uncommitted
        edits and refuses ones the update would overwrite.
        """
        if not updates:
            return
        truth = self._repository
        incoming: list[str] = []
        for number, (_, _, new) in enumerate(updates):
            if new is not None:
                _git(source, "update-ref", f"{_OUT}{number}", new)
                incoming.append(f"+{_OUT}{number}:{_IN}{number}")
        try:
            if incoming:
                _git(truth, "fetch", "--quiet", "--no-tags", str(source), *incoming)
            for ref, old, _ in updates:
                if _ref(truth, ref) != old:
                    raise KRepositoryMovedError(f"{ref} moved since it was read")
            checked_out = _git(truth, "symbolic-ref", "-q", "HEAD", check=False)
            for ref, old, new in updates:
                if (
                    ref == checked_out
                    and old is not None
                    and new is not None
                    and _git(truth, "rev-parse", f"{old}^{{tree}}")
                    != _git(truth, "rev-parse", f"{new}^{{tree}}")
                ):
                    _git(truth, "read-tree", "-m", "-u", old, new)
            _git(
                truth,
                "update-ref",
                "--stdin",
                stdin="".join(
                    f"delete {ref} {old}\n"
                    if new is None
                    else f"create {ref} {new}\n"
                    if old is None
                    else f"update {ref} {new} {old}\n"
                    for ref, old, new in updates
                ),
            )
        finally:
            for ref in _refs(truth, _IN):
                _git(truth, "update-ref", "-d", ref)
            for ref in _refs(source, _OUT):
                _git(source, "update-ref", "-d", ref)
        if prune:
            # Replaced snapshots and rewritten commits become unreachable;
            # remove them now rather than at Git's next automatic collection.
            _git(truth, "reflog", "expire", "--expire=now", "--all")
            _git(truth, "gc", "--quiet", "--prune=now")


_INDEX_FILTER = """
import json
import subprocess
import sys

plan = json.loads(open(sys.argv[1], encoding="utf-8").read())
compiled = set(plan["compiled"])
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
    if path in compiled or (value in citing and path not in blocking):
        current = None if path in compiled else tip.get(path)
        if current is not None and current[1] not in citing:
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
"""The index filter: drop compiled paths, pin needle-naming paths to their tip."""


def _history_entries(
    repository: Path, refs: tuple[str, ...]
) -> set[tuple[str, str, str]]:
    """Every (path, mode, object) that any commit of ``refs`` ever held."""
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
            *refs,
            "--",
        ),
        check=True,
        capture_output=True,
    ).stdout
    tokens = output.split(b"\0")
    entries: set[tuple[str, str, str]] = set()
    for index, token in enumerate(tokens):
        meta = token.strip(b"\n")
        if not meta.startswith(b":") or index + 1 >= len(tokens):
            continue
        _, mode, _, value, status = meta[1:].decode("ascii").split()
        if status != "D":
            path = tokens[index + 1].decode("utf-8", "surrogateescape")
            entries.add((path, mode, value))
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


def _refs(repository: Path, prefix: str = "") -> dict[str, str]:
    """Every ref under ``prefix`` and the object it names."""
    output = _git(
        repository,
        "for-each-ref",
        "--format=%(objectname) %(refname)",
        *((prefix,) if prefix else ()),
    )
    return {
        name: value
        for value, name in (line.split(" ", 1) for line in output.splitlines())
    }


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
