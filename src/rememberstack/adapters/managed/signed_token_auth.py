"""Verify signed keys at the engine perimeter (D136 §7.2–§7.4).

The reference signed-credential adapter for the D61 auth-perimeter port,
beside the self-host ``HashedBearerAuth``. It is operator-generic: any key
issuer that produces this claim contract works.

## Why a signature rather than a shared secret

``HashedBearerAuth`` compares a presented secret against **one** digest in
configuration: one credential per deployment. This adapter keeps the issuer's
**public keys** instead and checks a signature, so the number of credentials in
circulation stops being a property of the deployment's configuration, and a
compromised deployment can only verify credentials, never mint them.

## What a credential must carry

A bearer is ``<prefix>_<JWS>``, where the prefix is letters only (remember.dev
uses ``rmb_``) so secret scanners can recognise a leaked key. The JWS is
EdDSA-signed; its ``kid`` names a key in the fetched key set **and** in the
accepted revocation document's ``active_kids``. Common claims: ``iss`` equal
to the configured issuer, a single-string ``aud``, a non-empty ``jti`` not in
the revocation document, ``iat``/``nbf``/``exp``, ``permissions`` (an array of
strings) and ``kind``. Per kind:

========  ========================  =====================================  ==========
kind      ``aud``                   ``projects``                           ``sub``
========  ========================  =====================================  ==========
key       ``org:<tenant>``          ``"org:*"`` or 1–20 ids incl. this one a person
session   this deployment's id      exactly ``[this project]``             a person
service   this deployment's id      —                                      ``dpcred:<jti>``
========  ========================  =====================================  ==========

``key`` and ``session`` also carry ``org`` equal to the configured tenant.
``session`` may carry ``src`` (which host derived it); it is recorded for
audit and never decides authority. Any other ``aud`` — for example an OAuth
token meant for a hosted MCP server — is refused.

## Fail closed

Nothing signed is accepted without a fresh accepted revocation document
(:mod:`rememberstack.adapters.managed.perimeter_trust`). Every refusal is the
same opaque "no" to the caller; the reason class is logged with the ``jti``
once the signature has verified, never the token.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any
from uuid import UUID

import jwt

from rememberstack.adapters.managed.perimeter_trust import ACCEPTED_ALGORITHM
from rememberstack.adapters.managed.perimeter_trust import CLOCK_LEEWAY_SECONDS
from rememberstack.adapters.managed.perimeter_trust import PerimeterTrust
from rememberstack.adapters.managed.perimeter_trust import REVOCATION_TYPE
from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import CredentialKind
from rememberstack.model.auth import PerimeterScope

logger = logging.getLogger(__name__)

#: A compact JWS begins with a base64url JSON header, which always encodes to
#: ``eyJ`` (``{"``).
_JWS_START = "eyJ"

_LETTERS_PREFIX = re.compile(r"[A-Za-z]+")

#: The machine subject marker (D60): a ``service`` credential names itself.
_MACHINE_SUBJECT_PREFIX = "dpcred:"

#: ``projects`` value covering every project of the tenant, including ones
#: created after the key was minted.
_ALL_PROJECTS = "org:*"

_MAX_PROJECTS = 20

_COMMON_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "permissions",
    "kind",
    "iat",
    "nbf",
    "exp",
    "jti",
)

#: Memory permissions this build can bound, and the engine scope each grants.
_MEMORY_PERMISSIONS = {
    "memory:read": PerimeterScope.READ,
    "memory:write": PerimeterScope.WRITE,
    "memory:ingest": PerimeterScope.INGEST,
}


class SignedTokenUnusable(Exception):
    """This adapter refuses the presented credential; the message is the reason class."""


def strip_credential_prefix(*, presented: str) -> str:
    """Remove a letters-only ``<prefix>_`` in front of a JWS.

    Only when the bearer does not already start with a JWS header; everything
    up to and including the first ``_`` goes. A prefix that is not letters only
    is refused rather than guessed at.
    """
    if presented.startswith(_JWS_START):
        return presented
    prefix, separator, rest = presented.partition("_")
    if not separator or not _LETTERS_PREFIX.fullmatch(prefix):
        raise SignedTokenUnusable("malformed_prefix")
    return rest


def scope_for_permissions(*, permissions: object) -> PerimeterScope | None:
    """Map a credential's permissions to one engine scope (§7.4).

    Permissions without the ``memory:`` prefix belong to another service and are
    ignored. An unknown ``memory:`` permission is refused, never downgraded: a
    permission this build cannot bound is not treated as a narrower one.
    ``memory:write`` covers everything; otherwise exactly one of
    ``memory:read`` or ``memory:ingest`` may be present. ``None`` means no
    memory permission: the credential authenticates and may do nothing.
    """
    if not isinstance(permissions, list) or not all(
        isinstance(item, str) for item in permissions
    ):
        raise SignedTokenUnusable("malformed_permissions")
    memory = {item for item in permissions if item.startswith("memory:")}
    if memory - _MEMORY_PERMISSIONS.keys():
        raise SignedTokenUnusable("unknown_permission")
    if "memory:write" in memory:
        return PerimeterScope.WRITE
    if len(memory) > 1:
        raise SignedTokenUnusable("conflicting_permissions")
    if not memory:
        return None
    return _MEMORY_PERMISSIONS[memory.pop()]


class SignedTokenAuth:
    """Authenticate a signed key against the issuer's current trust documents."""

    def __init__(
        self,
        *,
        deployment_id: UUID,
        issuer: str,
        tenant_id: str,
        project_id: str,
        trust: PerimeterTrust,
    ) -> None:
        """Bind this deployment's identity at the issuer and its trust source."""
        self._deployment_id = deployment_id
        self._issuer = issuer
        self._tenant_id = tenant_id
        self._project_id = project_id
        self._trust = trust

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Verify a signed credential, or raise ``SignedTokenUnusable``."""
        if credential.scheme.lower() != "bearer":
            raise SignedTokenUnusable("unsupported_scheme")
        current = self._trust.current()
        if current is None:
            raise SignedTokenUnusable("stale_revocation")
        keys, document = current

        presented = credential.value.get_secret_value().decode("utf-8", errors="strict")
        token = strip_credential_prefix(presented=presented)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise SignedTokenUnusable("unreadable_header") from error
        if header.get("typ") == REVOCATION_TYPE:
            raise SignedTokenUnusable("wrong_type")
        kid = header.get("kid")
        # Named, never searched: trying every key would let a retired key keep
        # working through rotation.
        if not isinstance(kid, str) or kid not in keys:
            raise SignedTokenUnusable("unknown_kid")
        if kid not in document.active_kids:
            raise SignedTokenUnusable("retired_kid")

        try:
            claims = jwt.decode(
                token,
                key=keys[kid],  # type: ignore[arg-type]
                algorithms=[ACCEPTED_ALGORITHM],
                issuer=self._issuer,
                leeway=CLOCK_LEEWAY_SECONDS,
                options={
                    "require": list(_COMMON_CLAIMS),
                    "verify_signature": True,
                    "verify_iss": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    # The audience depends on `kind`; checked below.
                    "verify_aud": False,
                },
            )
        except jwt.PyJWTError as error:
            raise SignedTokenUnusable("invalid_token") from error

        token_id = claims["jti"]
        if not isinstance(token_id, str) or not token_id:
            raise SignedTokenUnusable("missing_jti")
        try:
            context = self._context(claims=claims, token_id=token_id)
            if token_id in document.revoked:
                raise SignedTokenUnusable("revoked")
        except SignedTokenUnusable as error:
            logger.info(
                "signed credential refused",
                extra={"reason": str(error), "jti": token_id},
            )
            raise
        return context

    def _context(
        self, *, claims: Mapping[str, Any], token_id: str
    ) -> AuthenticatedContext:
        """Apply the per-kind claim rules and narrow into the perimeter's vocabulary."""
        audience = claims["aud"]
        subject = claims["sub"]
        if not isinstance(audience, str):
            raise SignedTokenUnusable("malformed_aud")
        if not isinstance(subject, str) or not subject:
            raise SignedTokenUnusable("malformed_sub")
        scope = scope_for_permissions(permissions=claims["permissions"])

        kind = claims["kind"]
        source: str | None = None
        if kind == "key":
            if audience != f"org:{self._tenant_id}":
                raise SignedTokenUnusable("wrong_aud")
            self._check_org(claims=claims)
            projects = claims.get("projects")
            if projects != _ALL_PROJECTS and not (
                isinstance(projects, list)
                and 1 <= len(projects) <= _MAX_PROJECTS
                and all(isinstance(item, str) for item in projects)
                and self._project_id in projects
            ):
                raise SignedTokenUnusable("not_covered")
            credential_kind = CredentialKind.KEY
            person: str | None = subject
        elif kind == "session":
            if audience != str(self._deployment_id):
                raise SignedTokenUnusable("wrong_aud")
            self._check_org(claims=claims)
            if claims.get("projects") != [self._project_id]:
                raise SignedTokenUnusable("not_covered")
            if "src" in claims:
                source = claims["src"]
                if not isinstance(source, str) or not source:
                    raise SignedTokenUnusable("malformed_src")
            credential_kind = CredentialKind.BROWSER
            person = subject
        elif kind == "service":
            if audience != str(self._deployment_id):
                raise SignedTokenUnusable("wrong_aud")
            # A machine credential names itself; anything else would let a
            # payload choose which actor the audit records.
            if subject != f"{_MACHINE_SUBJECT_PREFIX}{token_id}":
                raise SignedTokenUnusable("wrong_sub")
            credential_kind = CredentialKind.DEPLOYMENT
            person = None
        else:
            raise SignedTokenUnusable("unknown_kind")

        return AuthenticatedContext(
            deployment_id=self._deployment_id,
            principal="signed-bearer",
            subject=person,
            credential_id=token_id,
            credential_kind=credential_kind,
            source=source,
            scope=scope,
        )

    def _check_org(self, *, claims: Mapping[str, Any]) -> None:
        if claims.get("org") != self._tenant_id:
            raise SignedTokenUnusable("wrong_org")
