"""A fake key issuer for perimeter tests (D136 §7): keys, credentials, revocation."""

from __future__ import annotations

from collections.abc import Mapping
import json
import time
from typing import Any
from uuid import UUID
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
from jwt.algorithms import OKPAlgorithm
from pydantic import SecretBytes

from rememberstack.adapters.managed.perimeter_trust import PerimeterTrust
from rememberstack.adapters.managed.signed_token_auth import SignedTokenAuth
from rememberstack.model import AuthenticatedContext
from rememberstack.model import PerimeterCredential

ISSUER = "https://issuer.example.test"
TENANT = "org-1"
JWKS_URL = f"{ISSUER}/jwks.json"
REVOCATION_URL = f"{ISSUER}/revocation"
MAX_AGE_S = 3_600.0


class MemoryStateStore:
    """The perimeter-state row, in memory, with the spine's compare-and-set."""

    def __init__(self) -> None:
        self.row: tuple[int, dict[str, Any]] | None = None
        self.saves = 0

    def load(self, *, deployment_id: UUID) -> Mapping[str, Any] | None:
        return None if self.row is None else dict(self.row[1])

    def save(
        self,
        *,
        deployment_id: UUID,
        expected_seq: int | None,
        seq: int,
        document: Mapping[str, Any],
    ) -> bool:
        current = None if self.row is None else self.row[0]
        if current != expected_seq or (current is not None and current >= seq):
            return False
        # Round-trip through JSON as jsonb would.
        self.row = (seq, json.loads(json.dumps(dict(document))))
        self.saves += 1
        return True


class FakeIssuer:
    """Signs keys and revocation documents, and serves them by URL."""

    def __init__(self, *, deployment_id: UUID, kids: tuple[str, ...] = ("k1",)) -> None:
        self.deployment_id = deployment_id
        self.private: dict[str, Ed25519PrivateKey] = {
            kid: Ed25519PrivateKey.generate() for kid in kids
        }
        self.published: list[str] = list(kids)
        self.revocation_token: str | None = None
        self.fail = False
        self.seq = 0

    def add_key(self, kid: str) -> None:
        self.private[kid] = Ed25519PrivateKey.generate()
        self.published.append(kid)

    def jwks(self) -> str:
        keys = []
        for kid in self.published:
            jwk = json.loads(OKPAlgorithm.to_jwk(self.private[kid].public_key()))
            jwk["kid"] = kid
            jwk["alg"] = "EdDSA"
            keys.append(jwk)
        return json.dumps({"keys": keys})

    def fetch(self, url: str) -> bytes:
        if self.fail:
            raise ConnectionError("issuer unreachable")
        if url == JWKS_URL:
            return self.jwks().encode()
        if url == REVOCATION_URL:
            assert self.revocation_token is not None, "no revocation document issued"
            return self.revocation_token.encode()
        raise AssertionError(f"unexpected fetch {url}")

    def revocation(
        self,
        *,
        revoked: tuple[str, ...] = (),
        active_kids: tuple[str, ...] | None = None,
        signer: str = "k1",
        seq: int | None = None,
        iat: float | None = None,
        audience: str | None = None,
        issuer: str = ISSUER,
        typ: str = "revocation+jwt",
    ) -> str:
        """Sign the next document and serve it; returns the token."""
        if seq is None:
            self.seq += 1
            seq = self.seq
        issued = int(time.time() if iat is None else iat)
        token = jwt.encode(
            {
                "iss": issuer,
                "aud": str(self.deployment_id) if audience is None else audience,
                "seq": seq,
                "iat": issued,
                "exp": issued + int(MAX_AGE_S),
                "revoked": list(revoked),
                "active_kids": list(
                    self.published if active_kids is None else active_kids
                ),
            },
            self.private[signer],
            algorithm="EdDSA",
            headers={"kid": signer, "typ": typ},
        )
        self.revocation_token = token
        return token

    def credential(
        self,
        *,
        kind: str = "key",
        permissions: object = ("memory:read",),
        kid: str = "k1",
        drop: tuple[str, ...] = (),
        prefix: str = "rmb_",
        kind_claim: str | None = None,
        **overrides: object,
    ) -> str:
        """A bearer value (``<prefix>_<JWS>``) with the claim set for ``kind``."""
        now = int(time.time())
        jti = uuid4().hex
        claims: dict[str, object] = {
            "iss": ISSUER,
            "sub": "person-1",
            "kind": kind,
            "permissions": list(permissions)  # type: ignore[call-overload]
            if isinstance(permissions, tuple)
            else permissions,
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": jti,
        }
        if kind == "key":
            claims.update(aud=f"org:{TENANT}", org=TENANT, projects="org:*")
        elif kind == "session":
            claims.update(
                aud=str(self.deployment_id),
                org=TENANT,
                projects=[str(self.deployment_id)],
            )
        else:
            claims.update(aud=str(self.deployment_id), sub=f"dpcred:{jti}")
        claims.update(overrides)
        if kind_claim is not None:
            claims["kind"] = kind_claim
        for name in drop:
            claims.pop(name, None)
        token = jwt.encode(
            claims, self.private[kid], algorithm="EdDSA", headers={"kid": kid}
        )
        return f"{prefix}{token}"


def build_trust(
    *, issuer: FakeIssuer, store: MemoryStateStore | None = None, clock: Any = time.time
) -> PerimeterTrust:
    return PerimeterTrust(
        issuer=ISSUER,
        deployment_id=issuer.deployment_id,
        signing_keys_url=JWKS_URL,
        revocation_url=REVOCATION_URL,
        refresh_s=60.0,
        max_age_s=MAX_AGE_S,
        store=store if store is not None else MemoryStateStore(),
        fetch=issuer.fetch,
        clock=clock,
    )


def build_auth(
    *, issuer: FakeIssuer, trust: PerimeterTrust, project_id: str | None = None
) -> SignedTokenAuth:
    return SignedTokenAuth(
        deployment_id=issuer.deployment_id,
        issuer=ISSUER,
        tenant_id=TENANT,
        project_id=project_id or str(issuer.deployment_id),
        trust=trust,
    )


def ready_auth(
    *, deployment_id: UUID | None = None
) -> tuple[FakeIssuer, SignedTokenAuth]:
    """An issuer with one key, a first document accepted, and the verifier."""
    issuer = FakeIssuer(deployment_id=deployment_id or uuid4())
    issuer.revocation()
    trust = build_trust(issuer=issuer)
    trust.refresh()
    return issuer, build_auth(issuer=issuer, trust=trust)


def present(auth: Any, bearer: str) -> AuthenticatedContext:
    return auth.authenticate(
        credential=PerimeterCredential(
            scheme="Bearer", value=SecretBytes(bearer.encode("utf-8"))
        )
    )
