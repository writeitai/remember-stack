"""D45/D61 remote interaction used by the single-writer Plane-K driver."""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from typing import runtime_checkable
from uuid import UUID

from rememberstack.model import KRevision


@runtime_checkable
class KGitRemotePort(Protocol):
    """Checkout and publish driver-owned Plane-K commits through one remote."""

    def checkout(self, *, destination: Path) -> KRevision:
        """Create the driver's checkout (branch plus compiled snapshot) and its revision."""
        ...

    def publish(self, *, worktree: Path, compiled: Mapping[str, UUID]) -> KRevision:
        """Publish the driver's tree and return the branch revision.

        Files at the ``compiled`` paths (each mapped to its artifact) form the
        compiled snapshot, which replaces
        the previous one and keeps no history (D159); every other file is
        committed to the branch with ordinary history. Publication refuses
        when the remote moved since ``checkout``.
        """
        ...
