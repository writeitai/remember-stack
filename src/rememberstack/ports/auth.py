"""D50/D60 auth-perimeter seam for a single-deployment trust domain."""

from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential


class AuthCredentialDeclined(Exception):
    """The provided credential was evaluated and refused by the perimeter."""


@runtime_checkable
class AuthPerimeterPort(Protocol):
    """Authenticate perimeter credentials without introducing internal tenancy."""

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Return the principal and deployment, or raise on credential refusal.

        Implementations may use ``AuthCredentialDeclined`` or ``ValueError``
        for refusal. Other exceptions indicate an operational failure.
        """
        ...
