"""Several ways to prove yourself, one perimeter, one refusal.

These also cover the wiring, because an adapter nobody instantiates is an
adapter that does not exist: the profile must actually build the composite when
both kinds of material are configured.
"""

from __future__ import annotations

import json
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
from jwt.algorithms import OKPAlgorithm
from pydantic import SecretBytes
from pydantic import SecretStr
import pytest

from rememberstack.adapters.managed.composite_auth import CompositeAuth
from rememberstack.adapters.managed.signed_token_auth import load_verification_keys
from rememberstack.adapters.managed.signed_token_auth import SignedTokenAuth
from rememberstack.adapters.managed.signed_token_auth import SignedTokenUnusable
from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import PerimeterScope
from rememberstack.profiles.selfhost import resolve_selfhost_api_auth
from rememberstack.profiles.selfhost import SelfHostSettings

_SECRET = "a-shared-self-host-secret"


def _keypair(*, kid: str) -> tuple[Ed25519PrivateKey, str]:
    """An Ed25519 private key and the JWKS that verifies it."""
    private = Ed25519PrivateKey.generate()
    jwk = json.loads(OKPAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = kid
    jwk["alg"] = "EdDSA"
    return private, json.dumps({"keys": [jwk]})


def _signed(*, private: Ed25519PrivateKey, kid: str, audience: str) -> str:
    """A credential the control plane would issue."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {
            "aud": audience,
            "sub": "member-1",
            "scope": "read",
            "iat": now,
            "nbf": now,
            "exp": now + datetime.timedelta(minutes=5),
            "jti": uuid4().hex,
        },
        private,
        algorithm="EdDSA",
        headers={"kid": kid},
    )


def _credential(*, secret: str) -> PerimeterCredential:
    """Present a secret the way the perimeter would."""
    return PerimeterCredential(
        scheme="Bearer", value=SecretBytes(secret.encode("utf-8"))
    )


def test_both_credential_kinds_reach_the_same_deployment() -> None:
    """The point of a composite: one set of routes, several callers."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    composite = CompositeAuth(
        adapters=(
            HashedBearerAuth(
                issued_deployment_id=deployment,
                digest=digest_bearer_secret(secret=_SECRET),
            ),
            SignedTokenAuth(
                deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
            ),
        )
    )

    shared = composite.authenticate(credential=_credential(secret=_SECRET))
    assert shared.deployment_id == deployment
    # A shared secret carries no subject and is unrestricted, as it always was.
    assert shared.subject is None
    assert shared.scope is PerimeterScope.WRITE

    token = _signed(private=private, kid="k1", audience=str(deployment))
    signed = composite.authenticate(credential=_credential(secret=token))
    assert signed.deployment_id == deployment
    assert signed.subject == "member-1"
    assert signed.scope is PerimeterScope.READ


def test_a_credential_no_adapter_accepts_is_one_refusal() -> None:
    """The refusal says nothing about which adapter came closest."""
    deployment = uuid4()
    _private, jwks = _keypair(kid="k1")
    composite = CompositeAuth(
        adapters=(
            HashedBearerAuth(
                issued_deployment_id=deployment,
                digest=digest_bearer_secret(secret=_SECRET),
            ),
            SignedTokenAuth(
                deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
            ),
        )
    )

    with pytest.raises(ValueError, match="unknown credential"):
        composite.authenticate(credential=_credential(secret="not-either-of-them"))


def test_a_composite_needs_an_adapter() -> None:
    """An empty composite would authenticate nothing and say so confusingly."""
    with pytest.raises(ValueError):
        CompositeAuth(adapters=())


def test_the_profile_composes_when_both_are_configured() -> None:
    """Wiring, not just parts: an adapter nobody builds does not exist."""
    deployment = uuid4()
    _private, jwks = _keypair(kid="k1")
    settings = SelfHostSettings(
        deployment_id=deployment,
        api_bearer_token=SecretStr(_SECRET),
        api_signing_keys=jwks,
    )

    auth = resolve_selfhost_api_auth(settings=settings)

    assert isinstance(auth, CompositeAuth)
    assert auth.authenticate(credential=_credential(secret=_SECRET)).scope is (
        PerimeterScope.WRITE
    )


def test_signing_keys_alone_are_a_perimeter() -> None:
    """A managed host may have no shared secret at all."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    settings = SelfHostSettings(
        deployment_id=deployment, api_signing_keys=jwks, require_api_auth=True
    )

    auth = resolve_selfhost_api_auth(settings=settings)

    assert isinstance(auth, SignedTokenAuth)
    token = _signed(private=private, kid="k1", audience=str(deployment))
    assert auth.authenticate(credential=_credential(secret=token)).subject == "member-1"


def test_require_api_auth_refuses_when_nothing_is_configured() -> None:
    """The check asks whether there is a perimeter, not which one."""
    settings = SelfHostSettings(deployment_id=uuid4(), require_api_auth=True)

    with pytest.raises(RuntimeError, match="REQUIRE_API_AUTH"):
        resolve_selfhost_api_auth(settings=settings)


def test_the_quickstart_still_has_no_perimeter() -> None:
    """Configuring nothing is still the open OSS quickstart (D60)."""
    assert (
        resolve_selfhost_api_auth(settings=SelfHostSettings(deployment_id=uuid4()))
        is None
    )


def test_an_unusable_key_set_refuses_to_start() -> None:
    """Half-loading a key set is a rotation that half works."""
    settings = SelfHostSettings(
        deployment_id=uuid4(), api_signing_keys='{"keys": [{"kty": "OKP"}]}'
    )

    with pytest.raises(RuntimeError, match="SIGNING_KEYS"):
        resolve_selfhost_api_auth(settings=settings)


def test_a_revoked_credential_is_refused_through_the_profile() -> None:
    """The deny-list travels as configuration, like the bind."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    revoked_id = uuid4().hex
    token = jwt.encode(
        {
            "aud": str(deployment),
            "sub": "member-1",
            "scope": "read",
            "iat": now,
            "nbf": now,
            "exp": now + datetime.timedelta(minutes=5),
            "jti": revoked_id,
        },
        private,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )
    settings = SelfHostSettings(
        deployment_id=deployment,
        api_signing_keys=jwks,
        api_revoked_credential_ids=f" {revoked_id} , other ",
    )

    auth = resolve_selfhost_api_auth(settings=settings)
    assert isinstance(auth, SignedTokenAuth)
    with pytest.raises(SignedTokenUnusable, match="revoked"):
        auth.authenticate(credential=_credential(secret=token))
