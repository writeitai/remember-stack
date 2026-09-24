"""What the signed-credential perimeter trusts, and how it stays current (D136 §7.5).

Two documents published by the key issuer decide whether a signed credential
can be accepted at all:

- the **key set** (a JWKS: the issuer's Ed25519 public keys, each named by a
  ``kid``), fetched from ``API_SIGNING_KEYS_URL``;
- the **revocation document**, a JWS the issuer signs for this one deployment
  and re-issues every refresh interval R. It lists the credential ids
  (``revoked``) refused despite a valid signature and the signing-key ids
  (``active_kids``) whose credentials remain valid.

This module fetches both every R, decides whether a newly fetched revocation
document may replace the one already accepted, persists the accepted document
in the spine so a restart cannot roll revocation back, and answers the one
question every request asks: *is there a fresh accepted document right now,
and with which keys?*

## Acceptance

- The first document on a deployment with nothing accepted is accepted when
  its signature verifies against the fetched key set and its ``iss`` and
  ``aud`` (this deployment's id) match.
- Every later document must also be signed by a ``kid`` that is active in the
  previously accepted document and carry a strictly greater ``seq``. The same
  ``seq`` with identical claims is a no-op; a lower ``seq``, or the same one
  with different claims, is a rollback attempt: rejected and logged.

The signer rule is what makes key rotation safe to rely on: once the issuer
drops a ``kid`` from ``active_kids``, a stolen private key of that generation
cannot sign a document that brings itself back.

## Freshness

A document is fresh until ``min(exp, iat + S)``. Without a fresh
document — before the first one is accepted, or once the accepted one ages
out, including one loaded from the spine at start-up that is already old —
every signed credential is refused. A key revoked at time *r* therefore stops
working by *r + S + leeway* (the 30 s credential clock tolerance) whatever
happens to fetches.

## Several API replicas

Each replica refreshes on its own, but they share the persisted row. A
refresh starts from whichever is newer, its own copy or the persisted one, and
the write is a compare-and-set on the stored ``seq`` the document was
verified against. A replica that loses the race reloads the row and verifies
its candidate again — including the signer rule against the peer's document —
so one replica can neither move the deployment backwards nor bring back a
signing key a peer's document retired. A document is published to
requests only after it is persisted.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
import threading
import time
from typing import Any
from typing import Protocol
from uuid import UUID

import httpx
import jwt
from jwt import PyJWK

logger = logging.getLogger(__name__)

#: The only accepted signature algorithm, named at every decode call so a
#: token cannot choose ``none`` or HMAC for itself.
ACCEPTED_ALGORITHM = "EdDSA"

#: Clock tolerance between the issuer and this host, in seconds, on a
#: credential's ``exp``/``nbf`` and a revocation document's ``iat``/``exp``.
CLOCK_LEEWAY_SECONDS = 30

#: The JWS header ``typ`` of a revocation document. Checked so a credential can
#: never be presented as a revocation document, or the other way round.
REVOCATION_TYPE = "revocation+jwt"

#: A fetched document larger than this is refused rather than read into memory.
_MAX_FETCH_BYTES = 4 * 1024 * 1024

_FETCH_TIMEOUT_SECONDS = 10.0

#: Whole-fetch deadline: a server dripping bytes cannot hold a refresh open.
_FETCH_DEADLINE_SECONDS = 10.0

#: Compare-and-set attempts per refresh before waiting for the next cycle.
_SAVE_ATTEMPTS = 3


class RevocationRejected(Exception):
    """A fetched revocation document may not replace the accepted one."""


@dataclass(frozen=True)
class RevocationDocument:
    """One accepted revocation document, reduced to what requests consult."""

    claims: Mapping[str, Any]
    seq: int
    iat: int
    exp: int
    revoked: frozenset[str]
    active_kids: frozenset[str]

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> RevocationDocument:
        """Validate the claim types; raise ``RevocationRejected`` on any mismatch."""
        seq, iat, exp = claims.get("seq"), claims.get("iat"), claims.get("exp")
        for name, value in (("seq", seq), ("iat", iat), ("exp", exp)):
            # `bool` is an `int` in Python; `true` is not a sequence number.
            if not isinstance(value, int) or isinstance(value, bool):
                raise RevocationRejected(
                    f"revocation document {name} is not an integer"
                )
        return cls(
            claims=dict(claims),
            seq=seq,  # type: ignore[arg-type]
            iat=iat,  # type: ignore[arg-type]
            exp=exp,  # type: ignore[arg-type]
            revoked=_string_set(claims=claims, name="revoked"),
            active_kids=_string_set(claims=claims, name="active_kids"),
        )

    def fresh_until(self, *, max_age_s: float) -> float:
        """The epoch second after which this document no longer vouches for anything."""
        return min(float(self.exp), self.iat + max_age_s)


def _string_set(*, claims: Mapping[str, Any], name: str) -> frozenset[str]:
    """An array-of-non-empty-strings claim as a set, or a rejection."""
    value = claims.get(name)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RevocationRejected(f"revocation document {name} is not a string array")
    return frozenset(value)


def verify_revocation_document(
    *,
    token: str,
    keys: Mapping[str, PyJWK],
    issuer: str,
    deployment_id: UUID,
    previous: RevocationDocument | None,
) -> RevocationDocument:
    """Verify one fetched document's signature, signer, issuer and audience.

    Sequence ordering against ``previous`` is the caller's decision; the signer
    rule is checked here because it needs the header.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise RevocationRejected("revocation document header is unreadable") from error
    if header.get("typ") != REVOCATION_TYPE:
        raise RevocationRejected("revocation document has the wrong typ")
    if header.get("alg") != ACCEPTED_ALGORITHM:
        raise RevocationRejected("revocation document is not EdDSA-signed")
    kid = header.get("kid")
    if not isinstance(kid, str) or kid not in keys:
        raise RevocationRejected("revocation document names an unknown key")
    if previous is not None and kid not in previous.active_kids:
        raise RevocationRejected(
            "revocation document is signed by a key the accepted document retired"
        )
    try:
        claims = jwt.decode(
            token,
            key=keys[kid],  # type: ignore[arg-type]
            algorithms=[ACCEPTED_ALGORITHM],
            audience=str(deployment_id),
            issuer=issuer,
            leeway=CLOCK_LEEWAY_SECONDS,
            options={
                "require": ["iss", "aud", "seq", "iat", "exp"],
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "verify_iat": True,
                # One audience, and it is this deployment: a document issued
                # for another deployment cannot be replayed here.
                "strict_aud": True,
            },
        )
    except jwt.PyJWTError as error:
        raise RevocationRejected(f"revocation document rejected: {error}") from error
    return RevocationDocument.from_claims(claims)


class PerimeterStateStore(Protocol):
    """The persisted last accepted revocation document (one row per deployment)."""

    def load(self, *, deployment_id: UUID) -> Mapping[str, Any] | None:
        """The accepted document's claims, or ``None`` when none was ever accepted."""
        ...

    def save(
        self,
        *,
        deployment_id: UUID,
        expected_seq: int | None,
        seq: int,
        document: Mapping[str, Any],
    ) -> bool:
        """Replace the stored document only if its ``seq`` is still ``expected_seq``.

        ``expected_seq=None`` means no row may exist yet. Returns ``False``
        without writing when another writer got there first.
        """
        ...


def fetch_bounded(url: str, *, deadline_s: float = _FETCH_DEADLINE_SECONDS) -> bytes:
    """GET ``url`` without following redirects, within a total deadline and size."""
    deadline = time.monotonic() + deadline_s
    timeout = min(_FETCH_TIMEOUT_SECONDS, deadline_s)
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"fetch returned HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > _MAX_FETCH_BYTES:
                raise ValueError("fetched document is too large")
            if time.monotonic() > deadline:
                raise TimeoutError("fetch exceeded its deadline")
    return bytes(body)


@dataclass(frozen=True)
class _Snapshot:
    keys: Mapping[str, PyJWK]
    document: RevocationDocument | None


class PerimeterTrust:
    """The fetched key set and the accepted revocation document, kept current."""

    def __init__(
        self,
        *,
        issuer: str,
        deployment_id: UUID,
        signing_keys_url: str,
        revocation_url: str,
        refresh_s: float,
        max_age_s: float,
        store: PerimeterStateStore,
        fetch: Callable[[str], bytes] = fetch_bounded,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Bind the issuer's URLs and the spine row; nothing is fetched yet."""
        self._issuer = issuer
        self._deployment_id = deployment_id
        self._signing_keys_url = signing_keys_url
        self._revocation_url = revocation_url
        self.refresh_s = refresh_s
        self._max_age_s = max_age_s
        self._store = store
        self._fetch = fetch
        self._clock = clock
        # Requests read this one attribute; a refresh replaces it whole, so a
        # request never sees keys from one refresh and a document from another.
        self._snapshot = _Snapshot(keys={}, document=None)
        self._refresh_lock = threading.Lock()

    def current(self) -> tuple[Mapping[str, PyJWK], RevocationDocument] | None:
        """The keys and the accepted document, or ``None`` when it is not fresh."""
        snapshot = self._snapshot
        document = snapshot.document
        if document is None:
            return None
        if self._clock() > document.fresh_until(max_age_s=self._max_age_s):
            return None
        return snapshot.keys, document

    def load(self) -> None:
        """Adopt the persisted document at start-up, fresh or not."""
        with self._refresh_lock:
            stored = self._load_stored()
            if stored is not None:
                self._snapshot = _Snapshot(keys=self._snapshot.keys, document=stored)
            self._log_if_not_fresh()

    def refresh(self) -> None:
        """Fetch the key set and the revocation document once; keep the last on failure."""
        with self._refresh_lock:
            keys = self._snapshot.keys
            try:
                keys = load_verification_keys(
                    jwks=self._fetch(self._signing_keys_url).decode("utf-8")
                )
            except Exception as error:  # noqa: BLE001 - keep the last good set
                logger.warning(
                    "signing key set fetch failed; keeping the last one",
                    extra={"reason": str(error)},
                )
            self._snapshot = _Snapshot(keys=keys, document=self._snapshot.document)
            try:
                self._refresh_document(keys=keys)
            except RevocationRejected as error:
                logger.warning(
                    "revocation document rejected; keeping the last accepted one",
                    extra={"reason": str(error)},
                )
            except Exception as error:  # noqa: BLE001 - keep the last accepted one
                logger.warning(
                    "revocation document refresh failed; keeping the last accepted one",
                    extra={"reason": str(error)},
                )
            self._log_if_not_fresh()

    def run(self, *, stop: threading.Event) -> None:
        """Refresh every R until ``stop`` is set."""
        while not stop.is_set():
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - the loop must outlive one bad cycle
                logger.exception("perimeter trust refresh failed")
            stop.wait(self.refresh_s)

    def _refresh_document(self, *, keys: Mapping[str, PyJWK]) -> None:
        """Accept a newer valid document, persisting it before requests see it.

        The write is a compare-and-set on the stored ``seq`` the candidate was
        verified against. When a peer replica commits first, the stored row is
        reloaded and the candidate verified again against it — its signer must
        be active in the document the peer accepted — before retrying.
        """
        token = self._fetch(self._revocation_url).decode("ascii").strip()
        for _ in range(_SAVE_ATTEMPTS):
            stored = self._load_stored()
            baseline = self._snapshot.document
            if stored is not None and (baseline is None or stored.seq > baseline.seq):
                # Another replica accepted a newer document: start from it.
                baseline = stored
                self._snapshot = _Snapshot(keys=keys, document=stored)
            candidate = verify_revocation_document(
                token=token,
                keys=keys,
                issuer=self._issuer,
                deployment_id=self._deployment_id,
                previous=baseline,
            )
            if baseline is not None:
                if (
                    candidate.seq == baseline.seq
                    and candidate.claims == baseline.claims
                ):
                    return
                if candidate.seq <= baseline.seq:
                    logger.error(
                        "revocation document rollback attempt rejected",
                        extra={"seq": candidate.seq, "accepted_seq": baseline.seq},
                    )
                    return
            if self._store.save(
                deployment_id=self._deployment_id,
                expected_seq=None if stored is None else stored.seq,
                seq=candidate.seq,
                document=candidate.claims,
            ):
                self._snapshot = _Snapshot(keys=keys, document=candidate)
                return
        raise RevocationRejected("perimeter state kept changing; retrying next cycle")

    def _load_stored(self) -> RevocationDocument | None:
        claims = self._store.load(deployment_id=self._deployment_id)
        if claims is None:
            return None
        return RevocationDocument.from_claims(claims)

    def _log_if_not_fresh(self) -> None:
        if self.current() is not None:
            return
        document = self._snapshot.document
        logger.error(
            "no fresh revocation document; every signed credential is refused",
            extra={
                "accepted_seq": None if document is None else document.seq,
                "max_age_s": self._max_age_s,
            },
        )


def load_verification_keys(*, jwks: str) -> dict[str, PyJWK]:
    """Parse a JWKS document into keys by ``kid``.

    An explicit ``{"keys": []}`` is an empty set: it denies every signed
    credential. Anything malformed raises ``ValueError`` rather than being
    skipped — a key set that silently loads three of four keys is a rotation
    that half works, discovered later by a caller holding the fourth.
    """
    try:
        document = json.loads(jwks)
    except json.JSONDecodeError as error:
        raise ValueError(f"verification key set is not usable: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("verification key set has no 'keys' array")
    declared = document["keys"]
    if not declared:
        return {}

    try:
        key_set = jwt.PyJWKSet.from_dict(document)
    except Exception as error:  # noqa: BLE001 - PyJWT raises several types here
        raise ValueError(f"verification key set is not usable: {error}") from error

    # PyJWT *skips* members it cannot use rather than failing; the counts must
    # agree.
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
        if not isinstance(kid, str) or not kid:
            # Selection looks up a string from the token's header, so a key
            # with a non-string kid could never be chosen.
            raise ValueError("every verification key must carry a string kid")
        if kid in keys:
            raise ValueError(f"verification key set repeats kid {kid!r}")
        _assert_ed25519_public_key(raw=raw, kid=kid)
        keys[kid] = key
    return keys


def _assert_ed25519_public_key(*, raw: dict[str, Any], kid: str) -> None:
    """Refuse anything that is not an Ed25519 *public* verification key.

    - **another curve**: an Ed448 key is still ``EdDSA`` and would verify, so
      the deployment would trust an algorithm nobody reviewed for it;
    - **private key material** (``d``): the signing key was published to every
      deployment, which could then mint the credentials it only checks;
    - **a key marked for something else** (``use: "enc"``, or ``key_ops``
      without ``verify``).
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
        # `"verify" in "verify"` is true, so a bare string must not pass.
        if not isinstance(key_ops, list) or not all(
            isinstance(operation, str) for operation in key_ops
        ):
            raise ValueError(f"verification key {kid!r} declares malformed key_ops")
        if "verify" not in key_ops:
            raise ValueError(f"verification key {kid!r} does not permit verification")
