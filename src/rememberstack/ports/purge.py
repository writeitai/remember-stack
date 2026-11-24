"""D159 erasure capabilities of the already-selected serving stores."""

from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol
from typing import runtime_checkable
from uuid import UUID

from rememberstack.model import KnowledgeErasure
from rememberstack.model import ObjectKey
from rememberstack.model import ObjectPurgeEvidence


@runtime_checkable
class ObjectPurgePort(Protocol):
    """Erase nominated objects, including retained versions and unfinished uploads."""

    def iter_purge_keys(self) -> Iterator[ObjectKey]:
        """Discover current, retained/deleted and unfinished object names.

        Unlike a reader listing this includes keys hidden by delete markers.
        Unsupported multipart listing keeps the provider-managed contract.
        """
        ...

    def purge_objects(
        self,
        *,
        keys: tuple[ObjectKey, ...],
        prefixes: tuple[ObjectKey, ...],
        before_write: Callable[[], None] | None = None,
        keep: tuple[ObjectKey, ...] = (),
    ) -> int:
        """Delete exact keys/prefixes, except the exact ``keep`` keys; absence succeeds.

        Returns how many objects, retained versions and unfinished uploads
        were removed, so a repeated purge of an erased set answers zero.
        """
        ...

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> ObjectPurgeEvidence | None:
        """Verify object absence, returning the unfinished-upload coverage."""
        ...

    def purge_retained_versions_containing(
        self, *, keys: tuple[ObjectKey, ...], needle: bytes
    ) -> int:
        """Delete retained (non-current) versions of these keys that contain ``needle``.

        A store that keeps no object versions answers zero. Current objects are
        never touched; the caller rewrites them.
        """
        ...


@runtime_checkable
class ProjectionPurgePort(Protocol):
    """Erase one document's corpus copies and the navigation that listed it."""

    def erase_document(self, *, deployment_id: UUID, doc_id: UUID) -> bool:
        """Delete its folders and rewrite navigation; whether anything was removed."""
        ...


class CorpusCachePurgePort(Protocol):
    """The composing host's controlled mount sessions, outside object storage."""

    def invalidate(
        self, *, deployment_id: UUID, doc_id: UUID, paths: tuple[str, ...]
    ) -> None:
        """Best effort: drop the document's paths and these rewritten files.

        Sessions keep running; an offline client catches up when it
        reconnects. A failure here never holds a forget open (D159 §3.7).
        """
        ...


@runtime_checkable
class KGitPurgePort(Protocol):
    """Erase forgotten text from the Plane-K repository (D159 §3.3 step 4)."""

    def erase(
        self,
        *,
        needles: tuple[str, ...],
        compiled: Mapping[str, UUID],
        drop: frozenset[UUID],
        accepted: Callable[[frozenset[UUID]], Mapping[UUID, str | None]],
        lease: AbstractContextManager[None],
    ) -> KnowledgeErasure:
        """Drop compiled pages and rewrite history that names a forgotten ID.

        ``compiled`` maps each live compiled path to its artifact. The pages of
        the ``drop`` artifacts leave the compiled snapshot, unless the body
        there is the one ``accepted`` (read under the lease) reports as the
        artifact's accepted rebuild. A current authored page or sidecar that
        names a needle blocks: it is reported and its history is kept until
        the owner edits it. Every other path whose history names a needle has
        that history rewritten to its current file (or removed when it no
        longer exists), and machine-written revisions of compiled paths leave
        the branch history (the pre-D159 cleanup). The work is prepared outside
        ``lease``, which is held only to publish; every pass also verifies the
        refs it read and collects unreachable objects.
        """
        ...
