"""The signed perimeter credential, and what it refuses.

These tests are mostly about refusal. A verifier that accepts good credentials
is easy; the interesting question is whether it can be talked into accepting
something it should not — a token signed with the wrong key, one aimed at
another deployment, one asking to be verified with no algorithm at all, or one
whose expiry has passed.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
from jwt.algorithms import OKPAlgorithm
from pydantic import SecretBytes
import pytest

from rememberstack.adapters.managed.signed_token_auth import load_verification_keys
from rememberstack.adapters.managed.signed_token_auth import SignedTokenAuth
from rememberstack.adapters.managed.signed_token_auth import SignedTokenUnusable
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import PerimeterScope


def _keypair(*, kid: str) -> tuple[Ed25519PrivateKey, str]:
    """An Ed25519 private key and the single-key JWKS that verifies it."""
    private = Ed25519PrivateKey.generate()
    public_jwk = json.loads(OKPAlgorithm.to_jwk(private.public_key()))
    public_jwk["kid"] = kid
    public_jwk["alg"] = "EdDSA"
    return private, json.dumps({"keys": [public_jwk]})


def _token(
    *,
    private: Ed25519PrivateKey,
    kid: str,
    audience: str,
    subject: str = "member-1",
    scope: str = "read",
    expires_in: timedelta = timedelta(minutes=5),
    algorithm: str = "EdDSA",
    **overrides: object,
) -> str:
    """Sign a credential the way the control plane would."""
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "aud": audience,
        "sub": subject,
        "scope": scope,
        "iat": now,
        "nbf": now,
        "exp": now + expires_in,
        "jti": uuid4().hex,
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm=algorithm, headers={"kid": kid})


def _present(auth: SignedTokenAuth, token: str):  # noqa: ANN202
    """Hand a token to the adapter as the perimeter would."""
    return auth.authenticate(
        credential=PerimeterCredential(
            scheme="Bearer", value=SecretBytes(token.encode("utf-8"))
        )
    )


def test_a_valid_credential_names_its_subject_and_scope() -> None:
    """The whole point: a person's browser reaches the deployment as itself."""
    deployment_id = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment_id, keys=load_verification_keys(jwks=jwks)
    )

    context = _present(
        auth, _token(private=private, kid="k1", audience=str(deployment_id))
    )

    assert context.deployment_id == deployment_id
    assert context.subject == "member-1"
    assert context.scope is PerimeterScope.READ
    # Machine traffic is recorded as machine traffic; the person rides alongside.
    assert context.principal == "signed-bearer"


def test_a_credential_for_another_deployment_is_refused() -> None:
    """D45: a wildcard certificate completes TLS to the wrong process."""
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=uuid4(), keys=load_verification_keys(jwks=jwks)
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, _token(private=private, kid="k1", audience=str(uuid4())))


def test_a_credential_signed_by_another_key_is_refused() -> None:
    """The signature is the whole basis of trust here."""
    _accepted, jwks = _keypair(kid="k1")
    # Same kid, different key: the attacker knows which key we expect and
    # cannot produce its signature.
    attacker, _ = _keypair(kid="k1")
    deployment = uuid4()
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    with pytest.raises(SignedTokenUnusable):
        _present(auth, _token(private=attacker, kid="k1", audience=str(deployment)))


def test_an_unknown_kid_is_refused_rather_than_searched() -> None:
    """Trying every key would keep rotated-away signatures working."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, _token(private=private, kid="k2", audience=str(deployment)))


def test_an_expired_credential_is_refused() -> None:
    """Expiry is the entire revocation story for a browser credential."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )

    with pytest.raises(SignedTokenUnusable):
        _present(
            auth,
            _token(
                private=private,
                kid="k1",
                audience=str(deployment),
                expires_in=timedelta(minutes=-10),
            ),
        )


def test_clock_leeway_admits_a_credential_that_just_expired() -> None:
    """Host drift must not produce a false 401 on a valid credential."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )

    context = _present(
        auth,
        _token(
            private=private,
            kid="k1",
            audience=str(deployment),
            expires_in=timedelta(seconds=-5),
        ),
    )
    assert context.subject == "member-1"


def test_an_unsigned_credential_is_refused() -> None:
    """The `alg: none` family. The algorithm is named by us, never by the token."""
    deployment = uuid4()
    _private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    now = datetime.now(timezone.utc)
    unsigned = jwt.encode(
        {
            "aud": str(deployment),
            "sub": "member-1",
            "scope": "write",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid4().hex,
        },
        key="",
        algorithm="none",
        headers={"kid": "k1"},
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, unsigned)


def test_a_revoked_credential_is_refused() -> None:
    """The deny-list, for credentials that outlive a single session."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    revoked = uuid4().hex
    auth = SignedTokenAuth(
        deployment_id=deployment,
        keys=load_verification_keys(jwks=jwks),
        revoked_ids=[revoked],
    )

    with pytest.raises(SignedTokenUnusable):
        _present(
            auth,
            _token(private=private, kid="k1", audience=str(deployment), jti=revoked),
        )


def test_a_credential_missing_required_claims_is_refused() -> None:
    """A missing claim is a missing constraint, not an unconstrained credential."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    now = datetime.now(timezone.utc)
    # No `aud`: the audience check cannot happen, so the credential is refused
    # rather than treated as valid for any deployment.
    no_audience = jwt.encode(
        {
            "sub": "member-1",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid4().hex,
        },
        private,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, no_audience)


def test_an_unknown_scope_is_refused_not_downgraded() -> None:
    """A credential claiming authority this build cannot bound is refused."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )

    with pytest.raises(SignedTokenUnusable):
        _present(
            auth,
            _token(
                private=private, kid="k1", audience=str(deployment), scope="superuser"
            ),
        )


def test_without_keys_the_adapter_is_inert() -> None:
    """Self-host configures none, and must go on working exactly as before."""
    auth = SignedTokenAuth(deployment_id=uuid4(), keys={})
    assert not auth.configured
    with pytest.raises(SignedTokenUnusable):
        _present(auth, "anything")


def test_a_key_set_without_kids_is_rejected_at_load() -> None:
    """Half-loading a key set is a rotation that half works."""
    private = Ed25519PrivateKey.generate()
    jwk = json.loads(OKPAlgorithm.to_jwk(private.public_key()))
    jwk["alg"] = "EdDSA"

    with pytest.raises(ValueError, match="kid"):
        load_verification_keys(jwks=json.dumps({"keys": [jwk]}))


def test_a_routing_prefixed_credential_is_accepted() -> None:
    """The control plane keeps `umc_dp_` in front of signed material.

    It needs the prefix to pick a verifier before parsing; a JWT parser cannot
    read it, because `umc_dp_eyJ…` is not valid JWS. Without stripping, every
    signed deployment token would be refused here — which the design asserted
    was fine, wrongly, until a review checked it.
    """
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token = _token(private=private, kid="k1", audience=str(deployment), scope="write")

    context = _present(auth, f"umc_dp_{token}")

    assert context.scope is PerimeterScope.WRITE
    assert context.deployment_id == deployment


def test_only_known_prefixes_are_stripped() -> None:
    """Stripping arbitrary leading bytes would be a parser bypass."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token = _token(private=private, kid="k1", audience=str(deployment))

    with pytest.raises(SignedTokenUnusable):
        _present(auth, f"anything_{token}")


def test_a_machine_credential_names_no_person() -> None:
    """`dpcred:` in the subject is a credential id, not somebody accountable.

    Recording it as the human subject would let an audit attribute a memory read
    to something that cannot answer for it.
    """
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token_id = uuid4().hex
    token = _token(
        private=private,
        kid="k1",
        audience=str(deployment),
        subject=f"dpcred:{token_id}",
        scope="write",
        jti=token_id,
    )

    context = _present(auth, f"umc_dp_{token}")

    assert context.subject is None
    assert context.credential_id == token_id


def test_a_browser_credential_still_names_its_person() -> None:
    """The distinction must not erase attribution where it exists."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )

    context = _present(
        auth, _token(private=private, kid="k1", audience=str(deployment))
    )

    assert context.subject == "member-1"
    assert context.credential_id is not None


def test_a_multi_audience_credential_is_refused() -> None:
    """One credential must not authenticate at two deployments.

    JWT permits `aud` to be a list, and the default is to accept when *any*
    entry matches. A control plane — or anyone who obtained signing authority —
    could then issue a single credential valid at several customers'
    deployments at once, which is exactly what D45's issued-deployment binding
    exists to prevent, and neither end could see it from the credential.

    Found by review, with a working token: the audience array below was
    accepted by verifiers bound to both deployments before `strict_aud`.
    """
    deployment = uuid4()
    other = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token = _token(
        private=private,
        kid="k1",
        audience=str(deployment),
        aud=[str(deployment), str(other)],
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, token)


def test_a_credential_with_an_empty_id_is_refused() -> None:
    """A credential with no id could never be revoked.

    The deny-list names ids. A credential presenting an empty one cannot appear
    in it, so it would stay usable for its whole life whatever the control
    plane decided — which makes the whole revocation mechanism decorative.
    """
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token = _token(
        private=private, kid="k1", audience=str(deployment), subject="dpcred:", jti=""
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, token)


def test_a_machine_subject_must_name_its_own_credential() -> None:
    """`dpcred:` is a claim about identity, and it is checked.

    Without this, a payload could name any credential it liked — or drop the
    delegating person from a credential that has one — and the audit record
    would faithfully report the lie.
    """
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token = _token(
        private=private,
        kid="k1",
        audience=str(deployment),
        subject="dpcred:not-the-jti",
        jti=uuid4().hex,
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, token)


def test_the_control_plane_routing_label_is_not_stripped_here() -> None:
    """`umc_cp_` addresses the control plane, which this perimeter is not.

    Accepting it would make the routing label decorative — the two credential
    kinds would become interchangeable at a verifier that reasons about only
    one of them.
    """
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token = _token(private=private, kid="k1", audience=str(deployment))

    with pytest.raises(SignedTokenUnusable):
        _present(auth, f"umc_cp_{token}")


def test_a_doubled_routing_label_is_refused() -> None:
    """Unwrapping repeatedly would accept a credential no issuer produced."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token = _token(private=private, kid="k1", audience=str(deployment))

    with pytest.raises(SignedTokenUnusable):
        _present(auth, f"umc_dp_umc_dp_{token}")


def test_the_credential_kind_names_the_audit_actor() -> None:
    """Audit needs to say *which credential*, not only which person."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    token_id = uuid4().hex
    machine = _present(
        auth,
        "umc_dp_"
        + _token(
            private=private,
            kid="k1",
            audience=str(deployment),
            subject=f"dpcred:{token_id}",
            jti=token_id,
        ),
    )
    browser = _present(
        auth, _token(private=private, kid="k1", audience=str(deployment))
    )

    assert machine.actor_id == f"dpcred:{token_id}"
    assert browser.actor_id is not None
    assert browser.actor_id.startswith("browsercred:")


def test_a_credential_without_a_scope_is_refused() -> None:
    """A credential silent about its authority must not receive some by default.

    Defaulting to `read` looked harmless and was not: it made "the issuer said
    nothing" and "the issuer said read-only" the same thing, so a signing bug
    that dropped the claim would produce working credentials nobody had decided
    the authority of.
    """
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "aud": str(deployment),
            "sub": "member-1",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid4().hex,
        },
        private,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, token)


def test_a_credential_without_nbf_is_refused() -> None:
    """D59 lists `nbf` among the claims a credential carries."""
    deployment = uuid4()
    private, jwks = _keypair(kid="k1")
    auth = SignedTokenAuth(
        deployment_id=deployment, keys=load_verification_keys(jwks=jwks)
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "aud": str(deployment),
            "sub": "member-1",
            "scope": "read",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid4().hex,
        },
        private,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )

    with pytest.raises(SignedTokenUnusable):
        _present(auth, token)


def test_a_key_set_that_half_loads_is_refused() -> None:
    """PyJWT skips members it cannot use; a rotation must not half apply.

    One good key beside one malformed one loaded as a set of one, so the
    deployment silently kept verifying with the outgoing key and rejected
    everything signed by the incoming one — discovered later, by a caller
    holding the half that was dropped.
    """
    _private, jwks = _keypair(kid="k1")
    document = json.loads(jwks)
    document["keys"].append({"kty": "OKP", "crv": "Ed25519", "kid": "k2"})

    with pytest.raises(ValueError, match="usable"):
        load_verification_keys(jwks=json.dumps(document))


def test_a_key_declaring_the_wrong_operations_is_refused() -> None:
    """`key_ops` is an array of strings, and a bare string must not pass.

    `"verify" in "verify"` is true, so a malformed declaration would have been
    read as permission it never gave.
    """
    _private, jwks = _keypair(kid="k1")
    document = json.loads(jwks)
    document["keys"][0]["key_ops"] = "verify"

    with pytest.raises(ValueError, match="malformed key_ops"):
        load_verification_keys(jwks=json.dumps(document))

    document["keys"][0]["key_ops"] = ["sign"]
    with pytest.raises(ValueError, match="permit verification"):
        load_verification_keys(jwks=json.dumps(document))


def test_a_key_set_carrying_private_material_is_refused() -> None:
    """A JWKS with `d` means the signing key was published to every deployment.

    Nothing downstream would notice: it verifies perfectly. The deployment
    would simply be able to mint the credentials it is supposed only to check,
    which is the one property this whole arrangement exists to prevent.

    A *valid* private JWK is used here on purpose — a malformed one is refused
    by the parser before this check is reached, which proves nothing.
    """
    private, _jwks = _keypair(kid="k1")
    private_jwk = json.loads(OKPAlgorithm.to_jwk(private))
    private_jwk["kid"] = "k1"
    private_jwk["alg"] = "EdDSA"
    assert private_jwk.get("d"), "the fixture must actually carry the seed"

    with pytest.raises(ValueError, match="private key material"):
        load_verification_keys(jwks=json.dumps({"keys": [private_jwk]}))


def test_a_key_with_a_non_string_kid_is_refused() -> None:
    """Selection looks up a string, so a numeric kid can never be chosen.

    It would load happily and then never match anything — a rotation that
    silently does nothing, discovered by whoever holds the credential signed
    with it.
    """
    _private, jwks = _keypair(kid="k1")
    document = json.loads(jwks)
    document["keys"][0]["kid"] = 7

    with pytest.raises(ValueError, match="string kid"):
        load_verification_keys(jwks=json.dumps(document))


def test_key_ops_members_must_all_be_strings() -> None:
    """An array containing an object is not the array RFC 7517 describes."""
    _private, jwks = _keypair(kid="k1")
    document = json.loads(jwks)
    document["keys"][0]["key_ops"] = ["verify", {"verify": True}]

    with pytest.raises(ValueError, match="malformed key_ops"):
        load_verification_keys(jwks=json.dumps(document))
