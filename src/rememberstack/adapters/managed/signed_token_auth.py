"""Verify signed perimeter credentials against published public keys.

The reference adapter for the D61 auth-perimeter port, beside the self-host
``HashedBearerAuth``.

## Why a signature rather than a shared secret

``HashedBearerAuth`` compares a presented secret against **one** digest handed
to the process as configuration. That is exactly right for a self-host
deployment and it has a hard ceiling: one credential. Reaching two means
pushing two digests, so every credential a customer mints becomes a deployment
event — configuration churn, a reload, and a window where the issuer and the
process disagree about what is valid.

This adapter inverts what the process holds. It keeps **public keys** and
checks a signature, so the number of credentials in circulation stops being a
property of the deployment's configuration. One key serves one caller or ten
thousand, and nothing has to be pushed before a freshly issued credential
works.

Public keys, not shared secrets, for a second reason: a deployment that is
compromised cannot mint credentials for itself. It can only verify.

## What it deliberately does not do

No table, no lookup, no user model, no notion of "which customer" beyond the
single deployment this process serves (D60/D61). Verification is arithmetic
over the presented bytes plus configuration held in memory.

The cost of that is honest and bounded: **a valid signature cannot be
withdrawn.** A credential is good until it expires, unless its id appears in
the revocation set this adapter is given. Browser credentials live minutes and
rely on expiry alone; long-lived credentials rely on the set.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import logging
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWK

from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import PerimeterScope

logger = logging.getLogger(__name__)

#: Only EdDSA is accepted. Naming the algorithm at the call site is what stops
#: an attacker choosing it for us — the "alg confusion" family of attacks,
#: where a token asks to be verified with ``none``, or with HMAC against a
#: public key everybody has.
_ACCEPTED_ALGORITHMS = ("EdDSA",)

#: Tolerance on ``exp`` and ``nbf``. A credential that lives minutes is
#: sensitive to drift between the issuer and this host, and a false 401 on a
#: genuinely valid credential is both alarming and hard to diagnose.
_CLOCK_LEEWAY_SECONDS = 30


class SignedTokenUnusable(Exception):
    """This adapter cannot serve the presented credential.

    Distinct from "the credential is invalid": it also covers "no keys are
    configured, so this adapter has nothing to say". The composite treats both
    the same way — try the next adapter — but the distinction matters when
    reading logs.
    """


class SignedTokenAuth:
    """Authenticate a JWT bearer against a set of published verification keys."""

    def __init__(
        self,
        *,
        deployment_id: UUID,
        keys: Mapping[str, PyJWK],
        revoked_ids: Sequence[str] = (),
    ) -> None:
        """Bind this process's deployment, its verification keys, and denials.

        ``keys`` is keyed by ``kid``. An empty mapping is legal and makes this
        adapter inert — a self-host deployment configures no keys and must go
        on working exactly as it did.
        """
        self._deployment_id = deployment_id
        self._keys = dict(keys)
        self._revoked_ids = frozenset(revoked_ids)

    @property
    def configured(self) -> bool:
        """True when this adapter has key material and can decide anything."""
        return bool(self._keys)

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Verify a signed credential, or raise.

        Order matters and is chosen so the cheapest refusals come first, and so
        no branch reveals more than "no".
        """
        if not self._keys:
            raise SignedTokenUnusable("no verification keys are configured")
        if credential.scheme.lower() != "bearer":
            raise SignedTokenUnusable("unsupported credential scheme")

        token = credential.value.get_secret_value().decode("utf-8", errors="strict")
        key = self._select_key(token=token)

        try:
            claims = jwt.decode(
                token,
                key=key,  # type: ignore[arg-type]
                algorithms=list(_ACCEPTED_ALGORITHMS),
                audience=str(self._deployment_id),
                leeway=_CLOCK_LEEWAY_SECONDS,
                options={
                    # Every one of these must be present. A credential that
                    # omits its audience or its expiry is not a credential this
                    # perimeter has ever agreed to accept, and treating a
                    # missing claim as "unconstrained" is how audience checks
                    # quietly stop happening.
                    "require": ["aud", "exp", "iat", "jti", "sub"],
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_signature": True,
                },
            )
        except jwt.PyJWTError as error:
            raise SignedTokenUnusable(f"credential rejected: {error}") from error

        return self._context(claims=claims)

    def _select_key(self, *, token: str) -> PyJWK:
        """Resolve the verification key named by the token's ``kid``.

        Named, never searched. Trying every key until one works would let a
        caller who obtained *any* accepted key sign for a deployment that key
        was rotated away from, and it turns key rotation into a period where
        old signatures silently keep working.
        """
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise SignedTokenUnusable("credential header is unreadable") from error

        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self._keys:
            raise SignedTokenUnusable("credential names an unknown key")
        return self._keys[kid]

    def _context(self, *, claims: Mapping[str, Any]) -> AuthenticatedContext:
        """Narrow verified claims into the perimeter's own vocabulary."""
        token_id = claims["jti"]
        if not isinstance(token_id, str) or token_id in self._revoked_ids:
            raise SignedTokenUnusable("credential is revoked")

        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise SignedTokenUnusable("credential names no subject")

        raw_scope = claims.get("scope", PerimeterScope.READ.value)
        try:
            scope = PerimeterScope(raw_scope)
        except ValueError as error:
            # An unrecognised scope is refused, never downgraded to the
            # narrowest known one: a credential asking for authority this build
            # does not understand is a credential this build cannot bound.
            raise SignedTokenUnusable("credential names an unknown scope") from error

        return AuthenticatedContext(
            deployment_id=self._deployment_id,
            principal="signed-bearer",
            subject=subject,
            scope=scope,
        )


def load_verification_keys(*, jwks: str) -> dict[str, PyJWK]:
    """Parse a JWKS document into keys by ``kid``.

    Raises ``ValueError`` on anything malformed rather than skipping it. A key
    set that silently loads three of four keys is a rotation that half works,
    discovered later by a caller holding the fourth.
    """
    try:
        key_set = jwt.PyJWKSet.from_json(jwks)
    except Exception as error:  # noqa: BLE001 - PyJWT raises several types here
        raise ValueError(f"verification key set is not usable: {error}") from error

    keys: dict[str, PyJWK] = {}
    for key in key_set.keys:
        kid = key.key_id
        if not kid:
            raise ValueError("every verification key must carry a kid")
        if kid in keys:
            raise ValueError(f"verification key set repeats kid {kid!r}")
        keys[kid] = key
    if not keys:
        raise ValueError("verification key set is empty")
    return keys
