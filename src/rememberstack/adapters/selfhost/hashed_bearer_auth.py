"""SHA-256 Bearer adapter for the single-deployment auth perimeter.

Verifier material is ``(issued_deployment_id, digest)``. The adapter returns
that issued UUID as ``AuthenticatedContext.deployment_id`` — never the process
UUID — so a bind for deployment A installed on process B is 403 at
``_perimeter``.
"""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential

_SHA256_HEX_LEN = 64


class HashedBearerAuth:
    """Authenticate one opaque Bearer against a single issued-deployment digest."""

    def __init__(self, *, issued_deployment_id: UUID, digest: bytes) -> None:
        """Bind the issued deployment UUID and the SHA-256 of its secret."""
        if len(digest) != 32:
            raise ValueError("Bearer digest must be a 32-byte SHA-256")
        self._issued_deployment_id = issued_deployment_id
        self._digest = digest

    @property
    def issued_deployment_id(self) -> UUID:
        """The deployment UUID encoded in the verifier material."""
        return self._issued_deployment_id

    def same_binding(self, *, other: HashedBearerAuth) -> bool:
        """Constant-time compare of issued UUID and digest."""
        return (
            self._issued_deployment_id == other._issued_deployment_id
            and hmac.compare_digest(self._digest, other._digest)
        )

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Return the issued deployment on digest match; raise otherwise."""
        if credential.scheme.lower() != "bearer":
            raise ValueError("unsupported credential scheme")
        presented = credential.value.get_secret_value()
        digest = hashlib.sha256(presented).digest()
        if not hmac.compare_digest(digest, self._digest):
            raise ValueError("unknown credential")
        return AuthenticatedContext(
            deployment_id=self._issued_deployment_id, principal="bearer"
        )


def parse_bearer_bind(*, bind: str) -> tuple[UUID, bytes]:
    """Parse ``{uuid}:{sha256hex}``. Raises ``ValueError`` if malformed."""
    stripped = bind.strip()
    issued_text, separator, digest_hex = stripped.partition(":")
    if not separator or not issued_text or not digest_hex:
        raise ValueError("API_BEARER_BIND must be {issued_deployment_uuid}:{sha256hex}")
    try:
        issued_deployment_id = UUID(issued_text)
    except ValueError as error:
        raise ValueError(
            "API_BEARER_BIND issued deployment id is not a UUID"
        ) from error
    hex_text = digest_hex.strip().lower()
    if len(hex_text) != _SHA256_HEX_LEN:
        raise ValueError("API_BEARER_BIND digest must be 64 hex characters")
    try:
        digest = bytes.fromhex(hex_text)
    except ValueError as error:
        raise ValueError("API_BEARER_BIND digest is not hex") from error
    if len(digest) != 32:
        raise ValueError("API_BEARER_BIND digest must be a 32-byte SHA-256")
    return issued_deployment_id, digest


def digest_bearer_secret(*, secret: str) -> bytes:
    """SHA-256 of the UTF-8 Bearer secret (same as UMC ``hash_token_secret``)."""
    return hashlib.sha256(secret.encode("utf-8")).digest()
