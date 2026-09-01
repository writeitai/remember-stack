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
import json
import logging
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWK

from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import CredentialKind
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

#: The one subject marker a data-plane credential may carry (D60). ``cpcred:``
#: names a *control-plane* credential, which this perimeter never verifies;
#: honouring it here would let the two kinds be used interchangeably at a
#: perimeter that reasons about only one of them.
_MACHINE_ACTOR_PREFIX = "dpcred:"

#: The routing label the control plane puts in front of signed material so it
#: can pick a verifier before parsing. It is not part of the JWT and must come
#: off before parsing — a prefixed token is not valid JWS. Only the data-plane
#: label is stripped here, for the same reason as above.
_CREDENTIAL_PREFIX = "umc_dp_"


def _strip_credential_prefix(*, presented: str) -> str:
    """Remove the data-plane routing label, if the credential carries one.

    ``umc_dp_eyJ…`` is not valid JWS, so the label has to come off before
    parsing. Exactly one pass, and only this exact label: ``umc_dp_umc_dp_<jws>``
    is not two labels around a token but a credential no issuer produced, and
    unwrapping repeatedly would accept it.
    """
    if presented.startswith(_CREDENTIAL_PREFIX):
        return presented[len(_CREDENTIAL_PREFIX) :]
    return presented


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

        presented = credential.value.get_secret_value().decode("utf-8", errors="strict")
        token = _strip_credential_prefix(presented=presented)
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
                    "require": ["aud", "exp", "iat", "nbf", "jti", "sub", "scope"],
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_signature": True,
                    # JWT allows `aud` to be a list, and PyJWT's default accepts
                    # the credential when *any* entry matches. That would make
                    # one signed credential usable at several deployments at
                    # once — precisely the property D45's issued-deployment
                    # binding exists to prevent, and one neither end could
                    # detect. Strict means one audience, and it is this
                    # deployment.
                    "strict_aud": True,
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
        if not isinstance(token_id, str) or not token_id:
            # An empty id is not merely untidy: a credential with no id cannot
            # be named in a revocation list, so it would stay usable for its
            # whole life whatever the control plane decided. Refusing it is what
            # makes the deny-list mean anything.
            raise SignedTokenUnusable("credential names no id")
        if token_id in self._revoked_ids:
            raise SignedTokenUnusable("credential is revoked")

        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise SignedTokenUnusable("credential names no subject")

        # A subject naming a credential is not a person. The control plane marks
        # the machine credential with a `dpcred:` prefix, and an audit that
        # recorded one in the human field would attribute a read to something
        # that cannot be accountable for it.
        #
        # The marker is not taken on trust. D60 fixes the machine subject as
        # `dpcred:` followed by the credential's own id, so a payload claiming
        # to be a machine while naming some *other* credential — or naming
        # nothing — is refused rather than recorded. Without this, a signed
        # credential could be attributed to whatever actor its payload chose,
        # which is an audit trail that cannot be relied on.
        machine_actor = subject.startswith(_MACHINE_ACTOR_PREFIX)
        if machine_actor and subject != f"{_MACHINE_ACTOR_PREFIX}{token_id}":
            raise SignedTokenUnusable("credential subject does not name itself")

        # Required above, so this is present. Read rather than defaulted,
        # because a default would mean a credential that says nothing about its
        # authority silently receives some — and "some" is a decision the
        # issuer should have to make explicitly.
        raw_scope = claims["scope"]
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
            subject=None if machine_actor else subject,
            credential_id=token_id,
            # Derived from the subject rather than carried as its own claim: a
            # newly required claim would reject every credential signed by a
            # control plane that predates it, and there is nothing to infer
            # wrongly, because the issuer — never the caller — writes `sub`.
            credential_kind=(
                CredentialKind.DEPLOYMENT if machine_actor else CredentialKind.BROWSER
            ),
            scope=scope,
        )


def load_verification_keys(*, jwks: str) -> dict[str, PyJWK]:
    """Parse a JWKS document into keys by ``kid``.

    Raises ``ValueError`` on anything malformed rather than skipping it. A key
    set that silently loads three of four keys is a rotation that half works,
    discovered later by a caller holding the fourth.
    """
    try:
        document = json.loads(jwks)
    except json.JSONDecodeError as error:
        raise ValueError(f"verification key set is not usable: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("verification key set has no 'keys' array")
    declared = document["keys"]
    if not declared:
        raise ValueError("verification key set is empty")

    try:
        key_set = jwt.PyJWKSet.from_dict(document)
    except Exception as error:  # noqa: BLE001 - PyJWT raises several types here
        raise ValueError(f"verification key set is not usable: {error}") from error

    # PyJWT *skips* members it cannot use rather than failing, so a set of one
    # good key and one malformed one loads as a set of one — which is a rotation
    # that half works, discovered later by a caller holding the other half. The
    # counts must agree.
    if len(key_set.keys) != len(declared):
        raise ValueError(
            f"verification key set declares {len(declared)} keys but only "
            f"{len(key_set.keys)} are usable"
        )

    keys: dict[str, PyJWK] = {}
    for key, raw in zip(key_set.keys, declared, strict=True):
        if not isinstance(raw, dict):
            raise ValueError("every verification key must be an object")
        kid = key.key_id
        if not kid:
            raise ValueError("every verification key must carry a kid")
        if kid in keys:
            raise ValueError(f"verification key set repeats kid {kid!r}")
        _assert_ed25519_public_key(raw=raw, kid=kid)
        keys[kid] = key
    return keys


def _assert_ed25519_public_key(*, raw: dict[str, Any], kid: str) -> None:
    """Refuse anything that is not an Ed25519 *public* verification key.

    D59 fixes the algorithm at EdDSA over Ed25519, and checking only the ``kid``
    let three wrong things through, each of which fails somewhere worse than
    here:

    - **another curve.** An Ed448 key is still ``EdDSA``, so it verifies happily
      and the deployment is now trusting an algorithm nobody reviewed for this
      perimeter.
    - **private key material.** A JWKS carrying ``d`` means the *signing* key
      was published to every deployment. Nothing downstream would notice; the
      deployment would simply be able to mint the credentials it is supposed
      only to check, which is the one property D59 was built to prevent.
    - **a key marked for something else.** ``use: "enc"``, or ``key_ops``
      without ``verify``, is a key its publisher said not to verify with.

    A misdeclared key set fails at load, where a person is looking, rather than
    at the next request.
    """
    if raw.get("kty") != "OKP" or raw.get("crv") != "Ed25519":
        raise ValueError(
            f"verification key {kid!r} must be an Ed25519 (OKP) key; "
            f"got kty={raw.get('kty')!r} crv={raw.get('crv')!r}"
        )
    if raw.get("d"):
        raise ValueError(
            f"verification key {kid!r} carries private key material; a key set "
            "published to deployments must contain public keys only"
        )
    use = raw.get("use")
    if use is not None and use != "sig":
        raise ValueError(f"verification key {kid!r} is declared for {use!r}, not 'sig'")
    key_ops = raw.get("key_ops")
    if key_ops is not None:
        # RFC 7517 says `key_ops` is an array of strings. A bare string, or an
        # object, would pass a naive `"verify" in key_ops` — `"verify" in
        # "verify"` is true, and so is membership in a dict with that key — so
        # a malformed declaration would be read as permission it never gave.
        if not isinstance(key_ops, list) or "verify" not in key_ops:
            raise ValueError(f"verification key {kid!r} does not permit verification")
