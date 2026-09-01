from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import types
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import jwt
from jwt.algorithms import OKPAlgorithm
from pydantic import SecretBytes, SecretStr
import pytest

from rememberstack.adapters.managed.signed_token_auth import load_verification_keys
from rememberstack.adapters.managed.signed_token_auth import SignedTokenAuth
from rememberstack.adapters.managed.signed_token_auth import SignedTokenUnusable
from rememberstack.model import PerimeterCredential
from rememberstack.surfaces import cli_main
from rememberstack.surfaces import cli as cli_module
from rememberstack.surfaces import credentials as credentials_module
from rememberstack.surfaces import device_login as device_login_module
from rememberstack.surfaces.cli import _retry_pending_revocation
from rememberstack.surfaces.credentials import append_pending_revocation
from rememberstack.surfaces.credentials import credential_lock
from rememberstack.surfaces.credentials import CredentialError
from rememberstack.surfaces.credentials import DurabilityUnconfirmed
from rememberstack.surfaces.credentials import load_credentials
from rememberstack.surfaces.credentials import load_pending_revocations
from rememberstack.surfaces.credentials import MAX_PENDING_REVOCATIONS
from rememberstack.surfaces.credentials import PendingRevocation
from rememberstack.surfaces.credentials import write_credentials


SURFACE_TESTS = Path(__file__).parent / "src" / "tests" / "surfaces"
sys.path.insert(0, str(SURFACE_TESTS))
import test_login as login_fixtures  # noqa: E402


def _keypair(kid: str = "k1") -> tuple[Ed25519PrivateKey, dict[str, object]]:
    private = Ed25519PrivateKey.generate()
    public = json.loads(OKPAlgorithm.to_jwk(private.public_key()))
    public.update({"kid": kid, "alg": "EdDSA"})
    return private, public


def _claims(deployment: UUID, **overrides: object) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "aud": str(deployment),
        "sub": "member-1",
        "scope": "read",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": uuid4().hex,
    }
    claims.update(overrides)
    return claims


def _present(auth: SignedTokenAuth, token: str):  # noqa: ANN202
    return auth.authenticate(
        credential=PerimeterCredential(
            scheme="Bearer", value=SecretBytes(token.encode("utf-8"))
        )
    )


def _signed(
    private: Ed25519PrivateKey,
    deployment: UUID,
    *,
    claims: dict[str, object] | None = None,
) -> str:
    return jwt.encode(
        claims or _claims(deployment),
        private,
        algorithm="EdDSA",
        headers={"kid": "k1"},
    )


def test_signed_claim_and_algorithm_attacks_are_refused() -> None:
    deployment = uuid4()
    other = uuid4()
    private, public = _keypair()
    auth = SignedTokenAuth(
        deployment_id=deployment,
        keys=load_verification_keys(jwks=json.dumps({"keys": [public]})),
    )

    attacks: list[str] = []
    attacks.append(
        _signed(
            private,
            deployment,
            claims=_claims(deployment, aud=[str(deployment), str(other)]),
        )
    )
    attacks.append(
        jwt.encode(
            _claims(deployment),
            key="",
            algorithm="none",
            headers={"kid": "k1"},
        )
    )
    attacks.append(
        jwt.encode(
            _claims(deployment),
            key=b"attacker-controlled-hmac-secret",
            algorithm="HS256",
            headers={"kid": "k1"},
        )
    )
    for bad_jti in ("", 7, [], {}):
        attacks.append(
            _signed(private, deployment, claims=_claims(deployment, jti=bad_jti))
        )
    attacks.append(
        _signed(
            private,
            deployment,
            claims=_claims(deployment, sub="dpcred:not-the-jti", jti=uuid4().hex),
        )
    )
    for missing in ("nbf", "scope"):
        claims = _claims(deployment)
        del claims[missing]
        attacks.append(_signed(private, deployment, claims=claims))

    valid = _signed(private, deployment)
    attacks.extend((f"umc_cp_{valid}", f"umc_dp_umc_dp_{valid}", f"unknown_{valid}"))

    for attack in attacks:
        with pytest.raises(SignedTokenUnusable):
            _present(auth, attack)

    assert _present(auth, f"umc_dp_{valid}").deployment_id == deployment


def test_key_set_and_key_ops_attacks_are_refused() -> None:
    private, public = _keypair()
    private_jwk = json.loads(OKPAlgorithm.to_jwk(private))
    private_jwk.update({"kid": "k1", "alg": "EdDSA"})

    malformed_sets: list[dict[str, object]] = [
        {"keys": []},
        {"keys": [public, {"kty": "OKP", "crv": "Ed25519", "kid": "k2"}]},
        {"keys": [{key: value for key, value in public.items() if key != "kid"}]},
        {"keys": [{**public, "kid": 7}]},
        {"keys": [public, {**public}]},
        {"keys": [private_jwk]},
        {"keys": [{**public, "use": "enc"}]},
        {"keys": [{**public, "key_ops": "verify"}]},
        {"keys": [{**public, "key_ops": {"verify": True}}]},
        {"keys": [{**public, "key_ops": ["verify", {"verify": True}]}]},
        {"keys": [{**public, "key_ops": ["sign"]}]},
    ]
    for document in malformed_sets:
        with pytest.raises(ValueError):
            load_verification_keys(jwks=json.dumps(document))


def _isolate(mp: pytest.MonkeyPatch, case: Path) -> None:
    case.mkdir(parents=True)
    login_fixtures._isolate_config(mp, case)
    mp.setattr(device_login_module.time, "sleep", lambda _seconds: None)


def _trace_login_case(
    case: Path, *, target: str, attack_index: int | None
) -> tuple[int, bool, bool, bool, int]:
    mp = pytest.MonkeyPatch()
    calls: list[str] = []
    count = 0
    try:
        _isolate(mp, case)
        login_fixtures._mock_client(
            mp,
            login_fixtures._grant_handler(
                token_body=login_fixtures._token_body(), calls=calls
            ),
        )

        def trace(frame, event: str, arg: object):  # noqa: ANN001, ANN202, ARG001
            nonlocal count
            if (
                frame.f_code.co_name == target
                and frame.f_code.co_filename == cli_module.__file__
            ):
                if event == "line":
                    current = count
                    count += 1
                    if attack_index == current:
                        raise KeyboardInterrupt
            return trace

        sys.settrace(trace)
        try:
            try:
                result = cli_main(
                    [
                        "login",
                        "--token-host",
                        login_fixtures._TOKEN_HOST,
                        "--api-url",
                        login_fixtures._API,
                    ]
                )
            except KeyboardInterrupt:
                result = 130
        finally:
            sys.settrace(None)

        current = load_credentials()
        current_names_mint = (
            current is not None and current.token_id == login_fixtures._NEW_TOKEN_ID
        )
        journal_names_mint = any(
            entry.token_id == login_fixtures._NEW_TOKEN_ID
            for entry in load_pending_revocations().entries
        )
        return (
            result,
            "token" in calls,
            "revoke" in calls,
            current_names_mint or journal_names_mint,
            count,
        )
    finally:
        sys.settrace(None)
        mp.undo()


def test_line_trace_attacks_on_record_leave_a_record_or_revoke(
    tmp_path: Path,
) -> None:
    *_, count = _trace_login_case(
        tmp_path / "baseline", target="record", attack_index=None
    )
    assert count > 5
    # Index zero is the already-reviewed first-bytecode residual.
    for index in range(1, count):
        _result, minted, revoked, named, _ = _trace_login_case(
            tmp_path / f"attack-{index}", target="record", attack_index=index
        )
        assert not minted or revoked or named, index


def test_line_trace_attacks_on_login_leave_a_record_file_or_revoke(
    tmp_path: Path,
) -> None:
    *_, count = _trace_login_case(
        tmp_path / "baseline", target="_login_locked", attack_index=None
    )
    assert count > 20
    for index in range(count):
        _result, minted, revoked, named, _ = _trace_login_case(
            tmp_path / f"attack-{index}",
            target="_login_locked",
            attack_index=index,
        )
        assert not minted or revoked or named, index


def _pending(token_id: UUID, host: str) -> PendingRevocation:
    return PendingRevocation(
        version=1,
        token_host=host,
        access_token=SecretStr(f"umc_dp_{token_id.hex}"),
        token_id=token_id,
    )


@pytest.mark.parametrize("failure", ["lock_open", "recovery_replace", "recovery_sync"])
def test_three_filesystem_failures_are_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    login_fixtures._isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(device_login_module.time, "sleep", lambda _seconds: None)
    real_open = credentials_module.os.open
    real_replace = credentials_module.os.replace

    if failure != "lock_open":
        append_pending_revocation(
            pending=_pending(UUID(int=1), "https://one.example.test")
        )
        append_pending_revocation(
            pending=_pending(UUID(int=2), "https://two.example.test")
        )
        login_fixtures._mock_client(
            monkeypatch, lambda _request: httpx.Response(204)
        )

    if failure == "lock_open":
        def fail_lock(path: object, *args: object, **kwargs: object):  # noqa: ANN202
            if str(path).endswith("/.lock"):
                raise OSError("lock open failed")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(credentials_module.os, "open", fail_lock)
    elif failure == "recovery_replace":
        def fail_journal_replace(source: object, target: object) -> None:
            if str(target).endswith("pending-revocation.json"):
                raise OSError("journal replace failed")
            real_replace(source, target)

        monkeypatch.setattr(credentials_module.os, "replace", fail_journal_replace)
    else:
        monkeypatch.setattr(
            credentials_module,
            "_fsync_directory",
            lambda _directory: (_ for _ in ()).throw(
                DurabilityUnconfirmed("journal directory sync failed")
            ),
        )

    assert cli_main(["login", "--token-host", login_fixtures._TOKEN_HOST]) == 1


def test_journal_capacity_refuses_before_mint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    login_fixtures._isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(device_login_module.time, "sleep", lambda _seconds: None)
    calls: list[str] = []
    grant = login_fixtures._grant_handler(
        token_body=login_fixtures._token_body(), calls=calls
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(503)
        return grant(request)

    login_fixtures._mock_client(monkeypatch, handler)
    for index in range(MAX_PENDING_REVOCATIONS - 1):
        append_pending_revocation(
            pending=_pending(UUID(int=index + 1), f"https://host-{index}.example.test")
        )

    assert cli_main(["login", "--token-host", login_fixtures._TOKEN_HOST]) == 1
    assert "authorize" not in calls


@pytest.mark.parametrize(
    ("current_host", "pending_host", "should_revoke"),
    [
        ("https://current.example.test", "https://other.example.test", True),
        ("https://faß.de", "https://xn--fa-hia.de", False),
        ("https://faß.de", "https://fass.de", True),
        ("https://example.com", "https://example.com.", False),
        ("https://example.com", "https://example.com..", True),
    ],
)
def test_cross_host_idna_and_trailing_dot_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    current_host: str,
    pending_host: str,
    should_revoke: bool,
) -> None:
    login_fixtures._isolate_config(monkeypatch, tmp_path)
    token_id = login_fixtures._TOKEN_ID
    write_credentials(
        credential=login_fixtures._stored(token_host=current_host, token_id=token_id)
    )
    append_pending_revocation(pending=_pending(token_id, pending_host))
    deletes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            deletes.append(str(request.url.host))
        return httpx.Response(204)

    login_fixtures._mock_client(monkeypatch, handler)
    _retry_pending_revocation()
    assert bool(deletes) is should_revoke


def test_windows_lock_path_uses_msvcrt_without_fchmod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    login_fixtures._isolate_config(monkeypatch, tmp_path)
    calls: list[int] = []
    fake = types.ModuleType("msvcrt")
    fake.LK_LOCK = 1  # type: ignore[attr-defined]
    fake.LK_UNLCK = 2  # type: ignore[attr-defined]
    fake.locking = lambda _handle, mode, _length: calls.append(mode)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.delattr(credentials_module.os, "fchmod")

    entered = False
    with credential_lock():
        entered = True
    assert entered
    assert calls == [fake.LK_LOCK, fake.LK_UNLCK]  # type: ignore[attr-defined]


def test_unconfirmed_rotation_must_not_drop_the_mint_record_on_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read-after-rename is visibility, not proof that directory metadata is durable."""
    login_fixtures._isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(device_login_module.time, "sleep", lambda _seconds: None)
    write_credentials(credential=login_fixtures._stored(token_prefix="old"))
    calls: list[str] = []
    login_fixtures._mock_client(
        monkeypatch,
        login_fixtures._grant_handler(
            token_body=login_fixtures._token_body(), calls=calls
        ),
    )
    real_write = credentials_module.write_credentials

    def unconfirmed(**kwargs: object) -> object:
        real_write(**kwargs)  # type: ignore[arg-type]
        raise DurabilityUnconfirmed("directory fsync failed after rename")

    monkeypatch.setattr(credentials_module, "write_credentials", unconfirmed)
    assert cli_main(
        [
            "login",
            "--token-host",
            login_fixtures._TOKEN_HOST,
            "--api-url",
            login_fixtures._API,
        ]
    ) == 0
    assert "revoke" in calls, "the predecessor was retired"
    outstanding = {entry.token_id for entry in load_pending_revocations().entries}
    assert login_fixtures._NEW_TOKEN_ID in outstanding
