"""Revocation documents, key rotation and fail-closed freshness (D136 §7.5)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import threading
import time
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from jwt.algorithms import OKPAlgorithm
import pytest

from rememberstack.adapters.managed.composite_auth import CompositeAuth
from rememberstack.adapters.managed.perimeter_trust import fetch_bounded
from rememberstack.adapters.managed.perimeter_trust import load_verification_keys
from rememberstack.adapters.managed.perimeter_trust import PerimeterTrust
from rememberstack.adapters.managed.perimeter_trust import RevocationRejected
from rememberstack.adapters.managed.perimeter_trust import verify_revocation_document
from rememberstack.adapters.managed.signed_token_auth import SignedTokenUnusable
from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.surfaces.http_api import build_api
from tests.signed_key_support import build_auth
from tests.signed_key_support import build_trust
from tests.signed_key_support import FakeIssuer
from tests.signed_key_support import ISSUER
from tests.signed_key_support import MAX_AGE_S
from tests.signed_key_support import MemoryStateStore
from tests.signed_key_support import present

_LEEWAY = 30


class _Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def _setup(
    *, kids: tuple[str, ...] = ("k1",)
) -> tuple[FakeIssuer, PerimeterTrust, MemoryStateStore, _Clock]:
    issuer = FakeIssuer(deployment_id=uuid4(), kids=kids)
    store = MemoryStateStore()
    clock = _Clock()
    return issuer, build_trust(issuer=issuer, store=store, clock=clock), store, clock


def _accepted_seq(trust: PerimeterTrust) -> int | None:
    current = trust.current()
    return None if current is None else current[1].seq


# --- acceptance -------------------------------------------------------------


def test_nothing_signed_is_accepted_before_the_first_document() -> None:
    issuer, trust, _store, _clock = _setup()
    auth = build_auth(issuer=issuer, trust=trust)
    bearer = issuer.credential(kind="key")
    for kind in ("key", "session", "service"):
        with pytest.raises(SignedTokenUnusable, match="stale_revocation"):
            present(auth, issuer.credential(kind=kind))

    # Keys fetched, but no document served yet: still closed.
    trust.refresh()
    with pytest.raises(SignedTokenUnusable, match="stale_revocation"):
        present(auth, bearer)

    issuer.revocation()
    trust.refresh()
    assert present(auth, bearer).subject == "person-1"


def test_the_first_document_is_accepted_and_heartbeats_advance_seq() -> None:
    issuer, trust, store, _clock = _setup()
    issuer.revocation()
    trust.refresh()
    assert _accepted_seq(trust) == 1
    assert store.row is not None and store.row[0] == 1

    for expected in (2, 3):
        issuer.revocation()
        trust.refresh()
        assert _accepted_seq(trust) == expected
        assert store.row is not None and store.row[0] == expected


def test_an_identical_document_is_a_no_op() -> None:
    issuer, trust, store, _clock = _setup()
    issuer.revocation()
    trust.refresh()
    trust.refresh()
    assert store.saves == 1
    assert _accepted_seq(trust) == 1


@pytest.mark.parametrize("seq", [1, 2])
def test_a_rollback_is_rejected_and_the_last_document_kept(seq: int) -> None:
    """A lower seq, or the same seq with different content, is a rollback."""
    issuer, trust, _store, _clock = _setup()
    issuer.revocation()
    issuer.revocation(revoked=("j-revoked",))
    trust.refresh()
    assert _accepted_seq(trust) == 2

    issuer.revocation(seq=seq)  # revoked list empty: different content
    trust.refresh()
    current = trust.current()
    assert current is not None
    assert current[1].seq == 2
    assert current[1].revoked == frozenset({"j-revoked"})


@pytest.mark.parametrize(
    "document",
    [
        {"audience": "another-deployment"},
        {"issuer": "https://other-issuer.example.test"},
        {"typ": "JWT"},
    ],
)
def test_a_document_for_another_audience_issuer_or_type_is_rejected(
    document: dict[str, str],
) -> None:
    issuer, trust, _store, _clock = _setup()
    issuer.revocation(**document)  # type: ignore[arg-type]
    trust.refresh()
    assert trust.current() is None

    issuer.revocation()
    trust.refresh()
    issuer.revocation(**document)  # type: ignore[arg-type]
    trust.refresh()
    assert _accepted_seq(trust) == 2


def test_a_document_signed_by_an_unknown_key_is_rejected() -> None:
    issuer, trust, _store, _clock = _setup()
    stranger = FakeIssuer(deployment_id=issuer.deployment_id)
    token = stranger.revocation()
    issuer.revocation_token = token
    trust.refresh()
    assert trust.current() is None


def test_a_later_document_signed_by_a_retired_key_is_rejected() -> None:
    """The signer rule: k2 was dropped from active_kids and cannot sign itself back."""
    issuer, trust, _store, _clock = _setup(kids=("k1", "k2"))
    issuer.revocation(active_kids=("k1",))
    trust.refresh()

    issuer.revocation(signer="k2", active_kids=("k1", "k2"))
    trust.refresh()
    current = trust.current()
    assert current is not None
    assert current[1].seq == 1
    assert current[1].active_kids == frozenset({"k1"})


def test_a_stale_document_is_not_accepted() -> None:
    issuer, trust, _store, _clock = _setup()
    issuer.revocation(iat=time.time() - 2 * MAX_AGE_S)
    trust.refresh()
    assert trust.current() is None


def test_verify_rejects_an_unreadable_document() -> None:
    issuer = FakeIssuer(deployment_id=uuid4())
    keys = load_verification_keys(jwks=issuer.jwks())
    with pytest.raises(RevocationRejected):
        verify_revocation_document(
            token="not-a-jws",
            keys=keys,
            issuer=ISSUER,
            deployment_id=issuer.deployment_id,
            previous=None,
        )


# --- revocation and rotation -----------------------------------------------


def test_a_revoked_credential_is_refused() -> None:
    issuer, trust, _store, _clock = _setup()
    auth = build_auth(issuer=issuer, trust=trust)
    bearer = issuer.credential(jti="j-revoked")
    other = issuer.credential()
    issuer.revocation()
    trust.refresh()
    assert present(auth, bearer)

    issuer.revocation(revoked=("j-revoked",))
    trust.refresh()
    with pytest.raises(SignedTokenUnusable, match="revoked"):
        present(auth, bearer)
    assert present(auth, other)


def test_a_kid_dropped_from_active_kids_is_refused_while_still_in_the_jwks() -> None:
    issuer, trust, _store, _clock = _setup(kids=("k1", "k2"))
    auth = build_auth(issuer=issuer, trust=trust)
    old = issuer.credential(kid="k1")
    new = issuer.credential(kid="k2")
    issuer.revocation(active_kids=("k1", "k2"))
    trust.refresh()
    assert present(auth, old) and present(auth, new)

    issuer.revocation(active_kids=("k2",))
    trust.refresh()
    assert "k1" in json.dumps(issuer.jwks())
    with pytest.raises(SignedTokenUnusable, match="retired_kid"):
        present(auth, old)
    assert present(auth, new)


def test_key_rotation_hands_signing_to_the_new_generation() -> None:
    issuer, trust, _store, _clock = _setup()
    auth = build_auth(issuer=issuer, trust=trust)
    issuer.revocation()
    trust.refresh()

    issuer.add_key("k2")  # published and active alongside k1
    issuer.revocation()
    trust.refresh()
    assert present(auth, issuer.credential(kid="k2"))

    issuer.revocation(signer="k2", active_kids=("k2",))  # k1 retired
    trust.refresh()
    issuer.published.remove("k1")  # a later JWKS drops it
    issuer.revocation(signer="k2", active_kids=("k2",))
    trust.refresh()
    assert _accepted_seq(trust) == 4
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(kid="k1"))
    assert present(auth, issuer.credential(kid="k2"))


def test_an_empty_key_set_denies_every_signed_credential() -> None:
    issuer, trust, _store, _clock = _setup()
    auth = build_auth(issuer=issuer, trust=trust)
    issuer.revocation()
    trust.refresh()
    bearer = issuer.credential()
    assert present(auth, bearer)

    issuer.published.clear()
    trust.refresh()
    with pytest.raises(SignedTokenUnusable, match="unknown_kid"):
        present(auth, bearer)


def test_a_failed_key_set_fetch_keeps_the_last_keys() -> None:
    issuer, trust, _store, _clock = _setup()
    auth = build_auth(issuer=issuer, trust=trust)
    issuer.revocation()
    trust.refresh()
    issuer.fail = True
    trust.refresh()
    assert present(auth, issuer.credential())


# --- freshness --------------------------------------------------------------


def test_every_kind_is_refused_once_the_document_is_stale() -> None:
    issuer, trust, _store, clock = _setup()
    auth = build_auth(issuer=issuer, trust=trust)
    issuer.revocation(iat=clock.now)
    trust.refresh()
    issuer.fail = True  # every later fetch fails

    clock.now += MAX_AGE_S - 1
    for kind in ("key", "session", "service"):
        assert present(auth, issuer.credential(kind=kind))
        trust.refresh()

    clock.now += 2
    for kind in ("key", "session", "service"):
        with pytest.raises(SignedTokenUnusable, match="stale_revocation"):
            present(auth, issuer.credential(kind=kind))


def test_a_revoked_key_stops_by_r_plus_s_plus_leeway_with_fetches_failing() -> None:
    """The worst case: the key is revoked at r, and no document arrives after."""
    issuer, trust, _store, clock = _setup()
    auth = build_auth(issuer=issuer, trust=trust)
    bearer = issuer.credential()
    issued = int(clock.now)
    issuer.revocation(iat=issued)
    trust.refresh()
    revoked_at = issued + 10  # after the last document it is not in
    issuer.fail = True

    clock.now = issued + MAX_AGE_S - 1
    assert present(auth, bearer)
    clock.now = issued + MAX_AGE_S + 1
    assert clock.now < revoked_at + MAX_AGE_S + _LEEWAY
    with pytest.raises(SignedTokenUnusable):
        present(auth, bearer)


def test_the_shared_secret_is_unaffected_by_a_missing_document() -> None:
    issuer, trust, _store, _clock = _setup()
    secret = "shared-secret"
    auth = CompositeAuth(
        adapters=(
            HashedBearerAuth(
                issued_deployment_id=issuer.deployment_id,
                digest=digest_bearer_secret(secret=secret),
            ),
            build_auth(issuer=issuer, trust=trust),
        )
    )
    client = TestClient(
        build_api(
            engine=object(),  # type: ignore[arg-type]
            deployment_id=issuer.deployment_id,
            admission=_Open(),  # type: ignore[arg-type]
            readiness=_Open(),  # type: ignore[arg-type]
            surface=_NoOperations(issuer.deployment_id),  # type: ignore[arg-type]
            connectors=object(),  # type: ignore[arg-type]
            auth=auth,
        )
    )
    signed = client.get(
        "/operations", headers={"Authorization": f"Bearer {issuer.credential()}"}
    )
    shared = client.get("/operations", headers={"Authorization": f"Bearer {secret}"})
    assert signed.status_code == 401
    assert shared.status_code == 200, shared.text


class _Open:
    def ensure_ready(self, *, deployment_id: object) -> tuple[object, ...]:
        return ()

    def assert_available(self, *, deployment_id: object) -> None:
        return None


class _NoOperations:
    def __init__(self, deployment_id: object) -> None:
        self.deployment_id = deployment_id

    def descriptors(self) -> tuple[object, ...]:
        return ()


# --- persistence and restart -----------------------------------------------


def test_the_persisted_seq_survives_a_restart() -> None:
    issuer, trust, store, clock = _setup()
    issuer.revocation()
    issuer.revocation()
    trust.refresh()

    restarted = build_trust(issuer=issuer, store=store, clock=clock)
    restarted.load()
    assert _accepted_seq(restarted) == 2

    issuer.revocation(seq=1)  # an old document replayed after the restart
    restarted.refresh()
    assert _accepted_seq(restarted) == 2


def test_a_stale_persisted_document_is_refused_at_start_up() -> None:
    issuer, trust, store, clock = _setup()
    auth_before = build_auth(issuer=issuer, trust=trust)
    issuer.revocation(iat=clock.now)
    trust.refresh()
    assert present(auth_before, issuer.credential())

    clock.now += MAX_AGE_S + 1
    issuer.fail = True
    restarted = build_trust(issuer=issuer, store=store, clock=clock)
    restarted.load()
    restarted.refresh()
    with pytest.raises(SignedTokenUnusable, match="stale_revocation"):
        present(build_auth(issuer=issuer, trust=restarted), issuer.credential())


def test_a_restarted_replica_needs_the_key_set_before_accepting() -> None:
    issuer, trust, store, clock = _setup()
    issuer.revocation()
    trust.refresh()
    restarted = build_trust(issuer=issuer, store=store, clock=clock)
    restarted.load()
    auth = build_auth(issuer=issuer, trust=restarted)
    with pytest.raises(SignedTokenUnusable, match="unknown_kid"):
        present(auth, issuer.credential())
    restarted.refresh()
    assert present(auth, issuer.credential())


def test_replicas_sharing_the_row_only_move_forward() -> None:
    """A replica starts from a peer's newer document and cannot store an older one."""
    issuer, first, store, clock = _setup()
    second = build_trust(issuer=issuer, store=store, clock=clock)
    issuer.revocation()
    first.refresh()
    second.refresh()
    issuer.revocation(revoked=("j1",))
    first.refresh()  # seq 2 stored by the first replica

    # The second replica is served a stale cached seq 1: it adopts the stored seq 2.
    issuer.revocation(seq=1)
    second.refresh()
    current = second.current()
    assert current is not None
    assert current[1].seq == 2
    assert current[1].revoked == frozenset({"j1"})
    assert store.row is not None and store.row[0] == 2


def test_a_replica_losing_the_race_rechecks_the_signer_against_the_peer() -> None:
    """A peer commits a document retiring k2 between our verify and our save.

    Our candidate (signed by k2, valid against the document we started from)
    must be verified again against the peer's document and rejected, rather
    than stored over it and bringing k2 back.
    """
    issuer, first, store, clock = _setup(kids=("k1", "k2"))
    second = build_trust(issuer=issuer, store=store, clock=clock)
    issuer.revocation(active_kids=("k1", "k2"))
    first.refresh()
    second.refresh()
    assert store.row is not None and store.row[0] == 1

    retiring = issuer.revocation(signer="k1", active_kids=("k1",), seq=2)
    candidate = issuer.revocation(signer="k2", active_kids=("k1", "k2"), seq=3)
    real_save = store.save
    raced = False

    def save_after_peer(**kwargs: Any) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            issuer.revocation_token = retiring
            first.refresh()  # the peer commits seq 2 (k2 retired) first
            issuer.revocation_token = candidate
        return real_save(**kwargs)

    store.save = save_after_peer  # type: ignore[method-assign]
    issuer.revocation_token = candidate
    second.refresh()

    assert raced
    assert store.row is not None and store.row[0] == 2
    assert store.row[1]["active_kids"] == ["k1"]
    current = second.current()
    assert current is not None
    assert current[1].seq == 2
    assert current[1].active_kids == frozenset({"k1"})


def test_a_replica_losing_the_race_retries_a_still_valid_candidate() -> None:
    issuer, first, store, clock = _setup()
    second = build_trust(issuer=issuer, store=store, clock=clock)
    issuer.revocation()
    first.refresh()
    second.refresh()
    peer = issuer.revocation(seq=2)
    candidate = issuer.revocation(seq=3)
    real_save = store.save
    raced = False

    def save_after_peer(**kwargs: Any) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            issuer.revocation_token = peer
            first.refresh()
            issuer.revocation_token = candidate
        return real_save(**kwargs)

    store.save = save_after_peer  # type: ignore[method-assign]
    issuer.revocation_token = candidate
    second.refresh()
    assert _accepted_seq(second) == 3
    assert store.row is not None and store.row[0] == 3


def test_a_slow_drip_fetch_is_cut_off_by_the_total_deadline() -> None:
    """Per-read timeouts never fire on a server sending one byte at a time."""

    class Drip(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            for _ in range(1000):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.02)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            fetch_bounded(f"http://127.0.0.1:{server.server_port}/", deadline_s=0.3)
        assert time.monotonic() - started < 2
    finally:
        server.shutdown()
        server.server_close()


def test_a_document_is_published_only_after_it_is_persisted() -> None:
    issuer, trust, store, _clock = _setup()

    def refuse(**_: object) -> bool:
        raise ConnectionError("spine unavailable")

    store.save = refuse  # type: ignore[method-assign]
    issuer.revocation()
    trust.refresh()
    assert trust.current() is None


# --- the key set -------------------------------------------------------------


def _jwks(kid: str = "k1") -> dict[str, list[dict[str, object]]]:
    private = Ed25519PrivateKey.generate()
    jwk = json.loads(OKPAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = kid
    jwk["alg"] = "EdDSA"
    return {"keys": [jwk]}


def test_an_empty_key_set_loads_as_no_keys() -> None:
    assert load_verification_keys(jwks='{"keys": []}') == {}


def test_a_key_set_that_half_loads_is_refused() -> None:
    document = _jwks()
    document["keys"].append({"kty": "OKP", "crv": "Ed25519", "kid": "k2"})
    with pytest.raises(ValueError, match="usable"):
        load_verification_keys(jwks=json.dumps(document))


def test_a_key_declaring_the_wrong_operations_is_refused() -> None:
    document = _jwks()
    document["keys"][0]["key_ops"] = "verify"
    with pytest.raises(ValueError, match="malformed key_ops"):
        load_verification_keys(jwks=json.dumps(document))
    document["keys"][0]["key_ops"] = ["verify", {"verify": True}]
    with pytest.raises(ValueError, match="malformed key_ops"):
        load_verification_keys(jwks=json.dumps(document))
    document["keys"][0]["key_ops"] = ["sign"]
    with pytest.raises(ValueError, match="permit verification"):
        load_verification_keys(jwks=json.dumps(document))


def test_a_key_set_carrying_private_material_is_refused() -> None:
    private = Ed25519PrivateKey.generate()
    private_jwk = json.loads(OKPAlgorithm.to_jwk(private))
    private_jwk["kid"] = "k1"
    assert private_jwk.get("d")
    with pytest.raises(ValueError, match="private key material"):
        load_verification_keys(jwks=json.dumps({"keys": [private_jwk]}))


def test_a_key_with_a_non_string_kid_is_refused() -> None:
    document = _jwks()
    document["keys"][0]["kid"] = 7
    with pytest.raises(ValueError, match="string kid"):
        load_verification_keys(jwks=json.dumps(document))


def test_a_repeated_kid_is_refused() -> None:
    document = _jwks()
    document["keys"].append(_jwks()["keys"][0])
    with pytest.raises(ValueError, match="repeats kid"):
        load_verification_keys(jwks=json.dumps(document))
