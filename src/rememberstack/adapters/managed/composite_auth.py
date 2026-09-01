"""Try several perimeter adapters in order, and fail as one.

A deployment can be reached by more than one kind of caller — a long-lived
shared secret from a self-host operator, a signed credential from the managed
control plane — and they are the same routes either way. The alternative,
a second copy of the API per credential kind, is how a system ends up with two
doors into one room and only notices when one of them rots.

So: one perimeter, several ways to prove yourself, and a single 401 when none
of them works.
"""

from __future__ import annotations

from collections.abc import Sequence
import logging

from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential
from rememberstack.ports.auth import AuthPerimeterPort

logger = logging.getLogger(__name__)


class CompositeAuth:
    """Authenticate against the first adapter that accepts the credential."""

    def __init__(self, *, adapters: Sequence[AuthPerimeterPort]) -> None:
        """Bind the adapters, in the order they should be tried."""
        if not adapters:
            raise ValueError("a composite perimeter needs at least one adapter")
        self._adapters = tuple(adapters)

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Return the first successful authentication, or raise.

        Every adapter is tried before refusing, and the refusal says nothing
        about which one came closest. An unauthenticated caller learning that
        their credential was "the right shape but expired" is being told about
        credentials they do not hold.

        The order is cheapest-first: a digest comparison is a hash and a memory
        compare, a signature check is public-key arithmetic. It also keeps the
        adapter that serves every credential in existence today ahead of the
        one that serves none yet.
        """
        for adapter in self._adapters:
            try:
                return adapter.authenticate(credential=credential)
            except Exception as error:  # noqa: BLE001 - adapters signal by raising
                logger.debug(
                    "perimeter adapter %s declined the credential: %s",
                    type(adapter).__name__,
                    error,
                )
        raise ValueError("unknown credential")
