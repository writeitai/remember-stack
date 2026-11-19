"""D51/D61 portable locations for the read-only corpus and standalone K."""

from typing import Protocol
from typing import runtime_checkable
from uuid import UUID

from rememberstack.model import PublishedMounts


@runtime_checkable
class MountPublisherPort(Protocol):
    """Expose corpus and optional Plane-K locations without mount mechanics."""

    def publish(self, *, deployment_id: UUID) -> PublishedMounts:
        """Return the read-only corpus and optional standalone K checkout."""
        ...
