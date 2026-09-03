"""D92 ``remember login`` / ``logout`` — CLI only, no SDK file pickup."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import UTC
import errno
import json
from pathlib import Path
import stat
import sys
from uuid import UUID

import httpx
from pydantic import SecretStr
import pytest

from rememberstack.surfaces import cli_main
from rememberstack.surfaces.credentials import CredentialFile
from rememberstack.surfaces.credentials import load_credentials
from rememberstack.surfaces.credentials import write_credentials
from rememberstack.surfaces.device_login import DEVICE_GRANT_TYPE
from rememberstack.surfaces.device_login import DeviceGrantError
from rememberstack.surfaces.device_login import request_same_origin
from rememberstack.surfaces.sdk import ClientSettings
from rememberstack.surfaces.sdk import MemoryClient

_TOKEN_HOST = "https://tokens.example.test"
_API = "https://query.example.test"
_ACCESS = "umc_dp_secret_value"
_TOKEN_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ORG_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_DEPLOYMENT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point credential IO at a temporary directory."""
    root = tmp_path / "rememberstack"
    root.mkdir()
    monkeypatch.setenv("REMEMBERSTACK_CONFIG_DIR", str(root))
    monkeypatch.delenv("REMEMBERSTACK_TOKEN_HOST", raising=False)
    monkeypatch.delenv("REMEMBERSTACK_API_URL", raising=False)
    monkeypatch.delenv("REMEMBERSTACK_API_AUTHORIZATION", raising=False)
    return root


def _stored(**overrides: object) -> CredentialFile:
    payload: dict[str, object] = {
        "version": 1,
        "api_url": _API,
        "token_host": _TOKEN_HOST,
        "access_token": _ACCESS,
        "token_type": "Bearer",
        "token_id": _TOKEN_ID,
        "org_id": _ORG_ID,
        "deployment_id": _DEPLOYMENT_ID,
        "label": "cli",
        "token_prefix": "umc_dp_sec",
    }
    payload.update(overrides)
    return CredentialFile.model_validate(payload)


def test_login_writes_0600_and_does_not_print_device_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Successful login stores owner-only JSON and prints only public fields."""
    _isolate_config(monkeypatch, tmp_path)
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/device/authorize":
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-SECRET",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://tokens.example.test/device",
                    "verification_uri_complete": "https://tokens.example.test/device?code=ABCD-EFGH",
                    "expires_in": 90,
                    "interval": 1,
                },
            )
        if request.url.path == "/v1/device/token":
            body = json.loads(request.content)
            assert body["grant_type"] == DEVICE_GRANT_TYPE
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(
                    400,
                    json={
                        "error": "authorization_pending",
                        "error_description": "waiting",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": _ACCESS,
                    "token_type": "Bearer",
                    "token_id": str(_TOKEN_ID),
                    "org_id": str(_ORG_ID),
                    "deployment_id": str(_DEPLOYMENT_ID),
                    "label": "cli",
                    "token_prefix": "umc_dp_sec",
                },
            )
        return httpx.Response(404)

    original = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    captured = capsys.readouterr()
    assert "DEVICE-SECRET" not in captured.out
    assert "DEVICE-SECRET" not in captured.err
    assert _ACCESS not in captured.out
    assert "ABCD-EFGH" in captured.out
    assert "umc_dp_sec" in captured.out
    stored = load_credentials()
    assert stored is not None
    path = tmp_path / "rememberstack" / "credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_text(encoding="utf-8")
    assert _ACCESS in raw
    assert "**********" not in raw
    assert stored.access_token.get_secret_value() == _ACCESS


def test_login_without_token_host_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Token host is required; it is never derived from --api-url."""
    _isolate_config(monkeypatch, tmp_path)
    assert cli_main(["login", "--api-url", "https://remember.dev/app/api/dp/v1"]) == 2
    assert "token-host" in capsys.readouterr().err
    assert load_credentials() is None


def test_client_settings_does_not_read_the_credential_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SDK defaults stay env/constructor only."""
    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    settings = ClientSettings.model_validate({})
    assert settings.api_authorization is None
    assert settings.api_url == "http://127.0.0.1:8000"
    client = MemoryClient.from_settings()
    assert "Authorization" not in client._client.headers  # noqa: SLF001


def test_logout_401_unlinks_and_503_keeps_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Already-dead tokens unlink; server errors keep the file."""
    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    status = {"code": 401}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/v1/api-tokens/self"
        return httpx.Response(status["code"])

    original = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    assert cli_main(["logout", "--token-host", _TOKEN_HOST]) == 0
    assert load_credentials() is None
    write_credentials(credential=_stored())
    status["code"] = 503
    assert cli_main(["logout", "--token-host", _TOKEN_HOST]) == 1
    assert load_credentials() is not None


def test_login_keeps_the_old_credential_when_the_grant_never_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A live credential survives a login that fails before minting.

    This is what D60's mint-before-revoke buys: the old order revoked first, so
    a login that then failed at the browser step left the machine with nothing.
    """
    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored(token_prefix="old-prefix"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(503)
        return httpx.Response(500)

    original = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    assert cli_main(["login", "--token-host", _TOKEN_HOST]) == 1
    stored = load_credentials()
    assert stored is not None
    assert stored.token_prefix == "old-prefix"


def test_cli_uses_file_after_env_and_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Flag > env > file. The constructed client carries the winner."""
    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    seen: list[tuple[str | None, str | None]] = []

    class _Probe:
        def __init__(
            self,
            *,
            base_url: str | None = None,
            authorization: str | None = None,
            **_: object,
        ) -> None:
            seen.append((base_url, authorization))

        def __enter__(self) -> _Probe:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def list_operations(self) -> tuple[object, ...]:
            return ()

        @classmethod
        def from_settings(cls) -> _Probe:
            raise AssertionError("file/env path must not call from_settings")

    monkeypatch.setattr("rememberstack.surfaces.cli.MemoryClient", _Probe)
    assert cli_main(["operations", "list"]) == 0
    assert seen[-1] == (_API, f"Bearer {_ACCESS}")
    monkeypatch.setenv("REMEMBERSTACK_API_URL", "https://env.example/api")
    monkeypatch.setenv("REMEMBERSTACK_API_AUTHORIZATION", "Bearer env-token")
    assert cli_main(["operations", "list"]) == 0
    assert seen[-1] == ("https://env.example/api", "Bearer env-token")
    assert (
        cli_main(
            [
                "operations",
                "list",
                "--api-url",
                "https://flag.example/api",
                "--token",
                "flag-token",
            ]
        )
        == 0
    )
    assert seen[-1] == ("https://flag.example/api", "Bearer flag-token")


def test_world_readable_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A world-readable credential file is not loaded."""
    root = _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    path = root / "credentials.json"
    path.chmod(0o644)
    with pytest.raises(Exception, match="world-readable"):
        load_credentials()


def test_same_origin_redirect_ok_cross_host_refused() -> None:
    """Authorize/token follow only host-preserving redirects."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/device/authorize":
            return httpx.Response(
                302, headers={"Location": "https://evil.example/steal"}
            )
        return httpx.Response(404)

    client = httpx.Client(
        base_url=_TOKEN_HOST,
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    with pytest.raises(DeviceGrantError, match="cross-host"):
        request_same_origin(client=client, method="POST", url="/v1/device/authorize")


def test_complete_flags_ignore_unreadable_credential_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Flags win even when the file is present and unreadable."""
    root = _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    (root / "credentials.json").chmod(0o644)
    seen: list[tuple[str | None, str | None]] = []

    class _Probe:
        def __init__(
            self,
            *,
            base_url: str | None = None,
            authorization: str | None = None,
            **_: object,
        ) -> None:
            seen.append((base_url, authorization))

        def __enter__(self) -> _Probe:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def list_operations(self) -> tuple[object, ...]:
            return ()

        @classmethod
        def from_settings(cls) -> _Probe:
            raise AssertionError("complete flags must not call from_settings")

    monkeypatch.setattr("rememberstack.surfaces.cli.MemoryClient", _Probe)
    assert (
        cli_main(
            [
                "operations",
                "list",
                "--api-url",
                "https://flag.example/api",
                "--token",
                "flag-token",
            ]
        )
        == 0
    )
    assert seen[-1] == ("https://flag.example/api", "Bearer flag-token")


def test_malformed_authorize_does_not_print_device_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validation failures must not echo device_code fragments."""
    _isolate_config(monkeypatch, tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "device_code": "DEVICE-SECRET-SHOULD-NOT-PRINT",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://tokens.example.test/device",
                "verification_uri_complete": "https://tokens.example.test/device",
                "expires_in": "not-an-int",
                "interval": 5,
            },
        )

    original = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    assert cli_main(["login", "--token-host", _TOKEN_HOST]) == 1
    captured = capsys.readouterr()
    assert "DEVICE-SECRET-SHOULD-NOT-PRINT" not in captured.out
    assert "DEVICE-SECRET-SHOULD-NOT-PRINT" not in captured.err


def test_write_credentials_creates_missing_parents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean nested config dir is created with 0700."""
    nested = tmp_path / "missing" / "rememberstack"
    monkeypatch.setenv("REMEMBERSTACK_CONFIG_DIR", str(nested))
    write_credentials(credential=_stored())
    assert nested.is_dir()
    assert stat.S_IMODE(nested.stat().st_mode) == 0o700
    assert (nested / "credentials.json").is_file()


def test_symlink_credentials_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reader must not follow a credentials.json symlink."""
    from rememberstack.surfaces.credentials import CredentialError

    root = _isolate_config(monkeypatch, tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    link = root / "credentials.json"
    link.symlink_to(target)
    with pytest.raises(CredentialError, match="symlink"):
        load_credentials()


def test_retry_after_cannot_shorten_slow_down() -> None:
    """Retry-After is a floor after the +5s bump, never a faster poll."""
    from rememberstack.surfaces.device_login import _retry_after_seconds

    response = httpx.Response(400, headers={"Retry-After": "1"})
    assert _retry_after_seconds(response=response, fallback=10.0, bump=True) == 15.0
    pending = httpx.Response(400, headers={"Retry-After": "8"})
    assert _retry_after_seconds(response=pending, fallback=5.0, bump=False) == 8.0
    zero = httpx.Response(400, headers={"Retry-After": "0"})
    assert _retry_after_seconds(response=zero, fallback=5.0, bump=False) == 5.0


def test_logout_without_file_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Idempotent logout when nothing is stored."""
    _isolate_config(monkeypatch, tmp_path)
    assert cli_main(["logout"]) == 0


#: The credential a fresh login receives, distinct from the one it replaces.
#:
#: They must differ: recovery refuses to revoke an entry naming the credential
#: currently on disk, because a crash between writing the journal and writing
#: the replacement leaves both naming the same token — and revoking there would
#: destroy the only credential the machine has.
_NEW_TOKEN_ID = UUID("11111111-2222-3333-4444-555555555555")


def _token_body(**overrides: object) -> dict[str, object]:
    """The device-token success body the control plane actually returns."""
    body: dict[str, object] = {
        "access_token": f"{_ACCESS}-new",
        "token_type": "Bearer",
        "token_id": str(_NEW_TOKEN_ID),
        "org_id": str(_ORG_ID),
        "deployment_id": str(_DEPLOYMENT_ID),
        "label": "cli",
        "token_prefix": "umc_dp_sec",
    }
    body.update(overrides)
    return body


def _grant_handler(*, token_body: dict[str, object], calls: list[str]) -> "object":
    """Serve authorize/token/revoke, recording the order they are called in."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            calls.append("revoke")
            return httpx.Response(204)
        if request.url.path == "/v1/device/authorize":
            calls.append("authorize")
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-SECRET",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://tokens.example.test/device",
                    "verification_uri_complete": "https://tokens.example.test/device?c=1",
                    "expires_in": 90,
                    "interval": 1,
                },
            )
        if request.url.path == "/v1/device/token":
            calls.append("token")
            return httpx.Response(200, json=token_body)
        return httpx.Response(404)

    return handler


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """Route every httpx.Client through the given handler."""
    original = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)  # type: ignore[arg-type]
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_login_derives_the_api_url_from_a_live_hostname(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A managed login needs only the token host once its deployment is live."""
    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    hostname = f"{_DEPLOYMENT_ID}.dp.remember.dev"
    _mock_client(
        monkeypatch,
        _grant_handler(
            token_body=_token_body(
                data_plane_hostname=hostname, data_plane_hostname_live=True
            ),
            calls=calls,
        ),
    )

    assert cli_main(["login", "--token-host", _TOKEN_HOST]) == 0
    stored = load_credentials()
    assert stored is not None
    assert stored.api_url == f"https://{hostname}"


def test_login_api_url_override_wins_over_an_unready_hostname(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Self-host and local users can explicitly choose their API endpoint."""
    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    _mock_client(
        monkeypatch,
        _grant_handler(
            token_body=_token_body(
                data_plane_hostname="not-live.dp.remember.dev",
                data_plane_hostname_live=False,
            ),
            calls=calls,
        ),
    )

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    stored = load_credentials()
    assert stored is not None
    assert stored.api_url == _API


def test_login_refuses_a_hostname_that_is_not_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Do not persist a managed endpoint before the control plane says it is live."""
    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    hostname = f"{_DEPLOYMENT_ID}.dp.remember.dev"
    _mock_client(
        monkeypatch,
        _grant_handler(
            token_body=_token_body(
                data_plane_hostname=hostname, data_plane_hostname_live=False
            ),
            calls=calls,
        ),
    )

    assert cli_main(["login", "--token-host", _TOKEN_HOST]) == 1
    captured = capsys.readouterr()
    assert hostname in captured.err
    assert "your deployment is not live yet" in captured.err
    assert "run `remember login` again when it is" in captured.err
    assert load_credentials() is None
    assert calls == ["authorize", "token", "revoke"]


def test_login_without_an_advertised_hostname_asks_for_api_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Older and self-hosted token services still need an explicit API URL."""
    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    _mock_client(monkeypatch, _grant_handler(token_body=_token_body(), calls=calls))

    assert cli_main(["login", "--token-host", _TOKEN_HOST]) == 1
    assert "--api-url" in capsys.readouterr().err
    assert load_credentials() is None
    assert calls == ["authorize", "token", "revoke"]


def test_login_revokes_the_predecessor_only_after_minting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The replacement is obtained first; the old credential dies after."""
    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored(token_prefix="old-prefix"))
    calls: list[str] = []
    _mock_client(monkeypatch, _grant_handler(token_body=_token_body(), calls=calls))

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    assert calls == ["authorize", "token", "revoke"]
    stored = load_credentials()
    assert stored is not None
    assert stored.token_prefix == "umc_dp_sec"


def test_login_stores_and_prints_the_expiry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``expires_at`` reaches the credential file and the operator's screen."""
    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    expiry = "2027-09-01T12:00:00+00:00"
    _mock_client(
        monkeypatch,
        _grant_handler(token_body=_token_body(expires_at=expiry), calls=calls),
    )

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    stored = load_credentials()
    assert stored is not None
    assert stored.expires_at == datetime(2027, 9, 1, 12, 0, tzinfo=UTC)
    assert "expires_at: 2027-09-01T12:00:00+00:00" in capsys.readouterr().out


def test_a_control_plane_without_expiry_leaves_the_field_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No expiry means *not recorded*, never *never expires*."""
    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    _mock_client(monkeypatch, _grant_handler(token_body=_token_body(), calls=calls))

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    stored = load_credentials()
    assert stored is not None
    assert stored.expires_at is None


def test_a_failed_predecessor_revoke_warns_and_keeps_the_new_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Login succeeded; a stale predecessor is a warning, not a failure."""
    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored(token_prefix="old-prefix"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(503)
        if request.url.path == "/v1/device/authorize":
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-SECRET",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://tokens.example.test/device",
                    "verification_uri_complete": "https://tokens.example.test/device?c=1",
                    "expires_in": 90,
                    "interval": 1,
                },
            )
        if request.url.path == "/v1/device/token":
            return httpx.Response(200, json=_token_body())
        return httpx.Response(404)

    _mock_client(monkeypatch, handler)
    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    captured = capsys.readouterr()
    assert "could not be revoked" in captured.err
    assert str(_TOKEN_ID) in captured.err
    stored = load_credentials()
    assert stored is not None
    assert stored.token_prefix == "umc_dp_sec"


def test_expiring_credential_warns_on_stderr_not_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The warning must not corrupt the machine-readable stream."""
    from rememberstack.surfaces.cli import _warn_if_expiring

    soon = datetime.now(tz=UTC) + timedelta(days=3)
    _warn_if_expiring(credential=_stored(expires_at=soon))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "expires on" in captured.err

    _warn_if_expiring(
        credential=_stored(expires_at=datetime.now(tz=UTC) - timedelta(days=1))
    )
    assert "expired on" in capsys.readouterr().err

    _warn_if_expiring(
        credential=_stored(expires_at=datetime.now(tz=UTC) + timedelta(days=200))
    )
    _warn_if_expiring(credential=_stored())
    assert capsys.readouterr().err == ""


def test_a_crash_between_write_and_revoke_leaves_a_retryable_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The predecessor's secret outlives the file that held it.

    Mint-before-revoke opens a window: the replacement is on disk, the old
    credential is still live at the control plane, and overwriting the file
    destroys the only copy of the old secret. Without a journal, a crash there
    strands a working credential nobody can name any more.
    """
    from rememberstack.surfaces.credentials import load_pending_revocations

    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored(token_prefix="old-prefix"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            raise httpx.ConnectError("the machine died here")
        if request.url.path == "/v1/device/authorize":
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-SECRET",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://tokens.example.test/device",
                    "verification_uri_complete": "https://tokens.example.test/device?c=1",
                    "expires_in": 90,
                    "interval": 1,
                },
            )
        if request.url.path == "/v1/device/token":
            return httpx.Response(200, json=_token_body())
        return httpx.Response(404)

    _mock_client(monkeypatch, handler)
    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    assert "still live" in capsys.readouterr().err

    journal = load_pending_revocations()
    assert len(journal.entries) == 1
    assert journal.entries[0].token_id == _TOKEN_ID
    assert journal.entries[0].access_token.get_secret_value() == _ACCESS


def test_a_404_does_not_count_as_a_revoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """404 can mean the route is absent, not that the credential is gone.

    Clearing the journal on it would quietly forget a live credential. 401 is
    different: the host resolved nothing for that bearer, which is the outcome
    we wanted by another name.
    """
    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import clear_pending_revocations
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    status = {"code": 404}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status["code"])

    _mock_client(monkeypatch, handler)
    entry = PendingRevocation(
        version=1,
        token_host=_TOKEN_HOST,
        access_token=SecretStr(_ACCESS),
        token_id=_TOKEN_ID,
    )
    append_pending_revocation(pending=entry)
    _retry_pending_revocation()
    assert load_pending_revocations().entries

    status["code"] = 401
    _retry_pending_revocation()
    assert not load_pending_revocations().entries

    append_pending_revocation(pending=entry)
    status["code"] = 200
    _retry_pending_revocation()
    assert not load_pending_revocations().entries
    clear_pending_revocations()


def test_a_naive_expiry_is_refused_rather_than_assumed_utc() -> None:
    """An absolute instant with no offset is a bug, not a UTC timestamp."""
    with pytest.raises(ValueError):
        _stored(expires_at=datetime(2027, 1, 1, 12, 0))


def test_recovery_never_revokes_the_credential_in_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crash before the replacement lands leaves both naming one token.

    The journal is written first, so an interruption before `write_credentials`
    leaves the journal and the credential file pointing at the same credential.
    Revoking it would destroy the only credential this machine has — so the
    entry is dropped instead: what it describes never happened.
    """
    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(204)

    _mock_client(monkeypatch, handler)
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host=_TOKEN_HOST,
            access_token=SecretStr(_ACCESS),
            token_id=_TOKEN_ID,
        )
    )

    _retry_pending_revocation()

    assert calls == []
    assert not load_pending_revocations().entries
    assert load_credentials() is not None


def test_a_second_login_does_not_forget_an_unresolved_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two credentials may be outstanding, and both must be retried.

    An earlier journal held one entry and was overwritten wholesale, so a login
    that followed an unreachable one forgot the first credential permanently.
    """
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    first = UUID("99999999-9999-9999-9999-999999999999")
    for token_id in (first, _TOKEN_ID):
        append_pending_revocation(
            pending=PendingRevocation(
                version=1,
                token_host=_TOKEN_HOST,
                access_token=SecretStr(_ACCESS),
                token_id=token_id,
            )
        )

    entries = load_pending_revocations().entries

    assert [entry.token_id for entry in entries] == [first, _TOKEN_ID]


def test_an_unreadable_journal_is_kept_not_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It names credentials that may still be live; deleting it loses them."""
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import pending_revocation_path

    root = _isolate_config(monkeypatch, tmp_path)
    path = pending_revocation_path()
    path.write_text("{ not json", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(CredentialError):
        load_pending_revocations()

    assert path.exists()
    assert root.exists()


def test_a_world_readable_journal_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It holds a bearer secret, so it gets the credential file's protections."""
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import CredentialError
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import pending_revocation_path
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host=_TOKEN_HOST,
            access_token=SecretStr(_ACCESS),
            token_id=_TOKEN_ID,
        )
    )
    pending_revocation_path().chmod(0o644)

    with pytest.raises(CredentialError, match="readable by others"):
        load_pending_revocations()


def test_an_unusable_token_host_does_not_stop_the_other_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One bad entry must not block recovery of the rest."""
    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    broken = UUID("88888888-8888-8888-8888-888888888888")
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host=":::",
            access_token=SecretStr(_ACCESS),
            token_id=broken,
        )
    )
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host=_TOKEN_HOST,
            access_token=SecretStr(_ACCESS),
            token_id=_TOKEN_ID,
        )
    )
    _mock_client(monkeypatch, lambda request: httpx.Response(204))

    _retry_pending_revocation()

    remaining = [entry.token_id for entry in load_pending_revocations().entries]
    assert remaining == [broken]
    assert "unusable token host" in capsys.readouterr().err


def test_logout_does_not_treat_404_as_a_revoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """404 may mean the route is absent; unlinking discards a live secret."""
    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    _mock_client(monkeypatch, lambda request: httpx.Response(404))

    assert cli_main(["logout", "--token-host", _TOKEN_HOST]) == 1
    assert load_credentials() is not None


def test_a_same_id_credential_from_another_host_is_still_revoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ids are unique per issuer, not globally.

    Comparing ids alone let a current credential from one token host cancel a
    pending revocation for a same-id credential on another — leaving that one
    live forever.
    """
    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    revoked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        revoked.append(str(request.url.host))
        return httpx.Response(204)

    _mock_client(monkeypatch, handler)
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host="https://other.example.test",
            access_token=SecretStr(_ACCESS),
            token_id=_TOKEN_ID,
        )
    )

    _retry_pending_revocation()

    assert revoked == ["other.example.test"]
    assert not load_pending_revocations().entries


def test_the_same_host_written_two_ways_is_one_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`https://HOST:443/` and `https://host` are the same server."""
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    for spelling in ("https://tokens.example.test", "https://TOKENS.example.test:443/"):
        append_pending_revocation(
            pending=PendingRevocation(
                version=1,
                token_host=spelling,
                access_token=SecretStr(_ACCESS),
                token_id=_TOKEN_ID,
            )
        )

    assert len(load_pending_revocations().entries) == 1


def test_a_full_journal_refuses_the_login_before_minting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Discovering a full journal after minting strands the new credential.

    The cap has to be checked while the only consequence is an error message.
    """
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import MAX_PENDING_REVOCATIONS
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    grant = _grant_handler(token_body=_token_body(), calls=calls)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            # Unreachable, so recovery cannot clear the journal first. A full
            # journal only happens when its entries genuinely cannot be retired.
            return httpx.Response(503)
        return grant(request)  # type: ignore[operator]

    _mock_client(monkeypatch, handler)
    for index in range(MAX_PENDING_REVOCATIONS - 1):
        append_pending_revocation(
            pending=PendingRevocation(
                version=1,
                token_host=f"https://host-{index}.example.test",
                access_token=SecretStr(_ACCESS),
                token_id=UUID(int=index + 1),
            )
        )

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 1

    assert "awaiting revocation" in capsys.readouterr().err
    # Nothing was minted: the grant was never started.
    assert "authorize" not in calls


def test_the_lock_refuses_rather_than_running_unlocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two concurrent logins leave one credential live and unrecorded.

    So being unable to serialise is a reason to refuse, not to proceed: a
    filesystem that cannot lock gets the same answer as a permission failure,
    because the consequence is the same.
    """
    import fcntl

    from rememberstack.surfaces.credentials import credential_lock
    from rememberstack.surfaces.credentials import CredentialError

    _isolate_config(monkeypatch, tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "this filesystem cannot lock")

    monkeypatch.setattr(fcntl, "flock", refuse)

    with pytest.raises(CredentialError, match="lock could not be taken"):
        with credential_lock():
            pass  # pragma: no cover - the lock must refuse before this runs


def test_equivalent_spellings_of_one_host_are_one_origin() -> None:
    """Each of these was a way to make one server look like two.

    And two entries for one server is not merely untidy: the live-credential
    guard compares origins, so a mismatch let recovery revoke the credential
    currently in use.
    """
    from rememberstack.surfaces.credentials import credential_origin

    canonical = credential_origin(token_host="https://tokens.example.test")
    for spelling in (
        "https://TOKENS.example.test",
        "https://tokens.example.test.",
        "https://tokens.example.test:443",
        "https://tokens.example.test/",
    ):
        assert credential_origin(token_host=spelling) == canonical, spelling

    compressed = credential_origin(token_host="https://[2001:db8::1]")
    expanded = credential_origin(token_host="https://[2001:0db8:0:0:0:0:0:1]")
    assert compressed == expanded

    unicode_name = "ünïcode.example.test"
    punycode_name = unicode_name.encode("idna").decode("ascii")
    assert punycode_name != unicode_name, "the fixture must actually differ"
    assert credential_origin(token_host=f"https://{unicode_name}") == credential_origin(
        token_host=f"https://{punycode_name}"
    )

    # And genuinely different hosts stay different.
    assert canonical != credential_origin(token_host="https://other.example.test")


def test_a_platform_without_file_locking_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sentinel file is not a lock, so there is no file-based fallback.

    It cannot tell a live owner from one that was killed, so it either wedges
    the user out permanently or reclaims a lock somebody is holding — and
    reclaiming by age did the second. An OS lock is released when the process
    dies, which is the property that makes it a lock at all; without one, the
    honest answer is to refuse.
    """
    from rememberstack.surfaces.credentials import credential_lock
    from rememberstack.surfaces.credentials import CredentialError

    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", None)

    with pytest.raises(CredentialError, match="no file locking"):
        with credential_lock():
            pass  # pragma: no cover - the lock must refuse before this runs


def test_a_lock_refusal_is_an_exit_code_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A user who cannot take the lock needs the reason, not a stack trace."""
    import fcntl

    _isolate_config(monkeypatch, tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "this filesystem cannot lock")

    monkeypatch.setattr(fcntl, "flock", refuse)

    assert cli_main(["login", "--token-host", _TOKEN_HOST]) == 1
    assert "lock could not be taken" in capsys.readouterr().err


def test_an_interrupt_anywhere_in_adoption_records_the_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Ctrl-C lands where it lands; guarding each call left the gaps.

    An interrupt after the response converted and before the journal was
    written produced a live bearer with no file, no journal entry, and no
    attempt to withdraw it.
    """
    from rememberstack.surfaces import credentials as credentials_module
    from rememberstack.surfaces.credentials import load_pending_revocations

    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    _mock_client(monkeypatch, _grant_handler(token_body=_token_body(), calls=calls))

    def interrupted(*args: object, **kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(credentials_module, "write_credentials", interrupted)

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 130

    # The credential is live at the token host, so it is named in the journal
    # — the earlier assertion here was inverted and passed when *nothing*
    # happened, which is exactly the failure it was supposed to catch.
    outstanding = {entry.token_id for entry in load_pending_revocations().entries}
    assert _NEW_TOKEN_ID in outstanding


def test_idna2008_equivalent_hosts_compare_equal() -> None:
    """Canonicalisation must agree with the HTTP client, not with Python's codec.

    Python's built-in `encode("idna")` is IDNA2003 and maps `ß` to `ss`, which
    both merges two hosts httpx treats as different and splits one it treats as
    the same. The split is the dangerous direction: it made the live-credential
    guard miss, and recovery revoked the credential in use.
    """
    from rememberstack.surfaces.credentials import credential_origin

    assert httpx.URL("https://faß.de").host == httpx.URL("https://xn--fa-hia.de").host
    assert credential_origin(token_host="https://faß.de") == credential_origin(
        token_host="https://xn--fa-hia.de"
    )
    # And two hosts httpx keeps apart must stay apart.
    assert httpx.URL("https://faß.de").host != httpx.URL("https://fass.de").host
    assert credential_origin(token_host="https://faß.de") != credential_origin(
        token_host="https://fass.de"
    )


def test_the_mint_is_recorded_before_the_caller_can_see_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The window between minting and recording is closed at its source.

    A caller cannot guard the boundary between `poll_device_token` returning
    and its own next statement — CPython leaves it outside any exception
    region — so the record is written inside that function, before the value is
    visible to anyone.
    """
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.device_login import poll_device_token

    _isolate_config(monkeypatch, tmp_path)
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_token_body())

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url=_TOKEN_HOST, transport=transport) as client:
        token = poll_device_token(
            client=client,
            device_code="DEVICE-SECRET",
            interval=0,
            expires_in=5,
            sleep=lambda _seconds: None,
            on_minted=seen.append,
        )

    assert seen == [token], "the callback runs before the value is returned"
    assert not load_pending_revocations().entries, "the callback is the caller's"


def test_a_record_that_cannot_be_written_gives_the_credential_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The callback is the last point that knows the secret.

    An interrupt or an IO failure inside it used to escape with the mint
    untracked, and nothing later could notice — the credential existed only at
    the token host, named by nothing on this machine.
    """
    from rememberstack.surfaces import credentials as credentials_module

    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    _mock_client(monkeypatch, _grant_handler(token_body=_token_body(), calls=calls))

    def refuse(*args: object, **kwargs: object) -> object:
        raise OSError("the disk is full")

    monkeypatch.setattr(credentials_module, "append_pending_revocation", refuse)

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 1

    # It could not be recorded, so it was withdrawn instead.
    assert "revoke" in calls


def test_only_one_trailing_dot_is_the_dns_root() -> None:
    """`example.com.` is the same host; `example.com..` is not one httpx accepts.

    Merging them let recovery drop a pending revocation for a credential it had
    not revoked.
    """
    from rememberstack.surfaces.credentials import credential_origin

    assert credential_origin(token_host="https://example.com.") == credential_origin(
        token_host="https://example.com"
    )
    assert credential_origin(token_host="https://example.com..") != credential_origin(
        token_host="https://example.com"
    )


def test_an_unconfirmed_write_keeps_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rename that may not have landed must not discard the only record.

    The file is written and names the new credential, so unwinding would revoke
    something the machine is using — but a power loss could still restore the
    old file while the credential stays live at the token host. Keeping the
    entry lets the next command resolve it after reading the file back, which
    is proof the write survived.
    """
    from rememberstack.surfaces import credentials as credentials_module
    from rememberstack.surfaces.credentials import DurabilityUnconfirmed
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import write_credentials as real_write

    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    _mock_client(monkeypatch, _grant_handler(token_body=_token_body(), calls=calls))

    def unconfirmed(**kwargs: object) -> object:
        real_write(**kwargs)  # type: ignore[arg-type]
        raise DurabilityUnconfirmed("the directory could not be synced")

    monkeypatch.setattr(credentials_module, "write_credentials", unconfirmed)

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    assert "could not be synced" in capsys.readouterr().err

    outstanding = {entry.token_id for entry in load_pending_revocations().entries}
    assert _NEW_TOKEN_ID in outstanding, "the record must outlive an unsure write"
    # The credential is usable, and a later command resolves the record by
    # reading the file back.
    assert load_credentials() is not None


def test_recovery_confirms_durability_before_forgetting_a_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reading the file back is visibility, not durability.

    A power loss can lose a directory entry while every read in this process
    succeeds, so "I can see it" is not proof the write survived. The sync is
    re-attempted, and only its success justifies forgetting the record.
    """
    from rememberstack.surfaces import credentials as credentials_module
    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import DurabilityUnconfirmed
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    _mock_client(monkeypatch, lambda request: httpx.Response(204))
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host=_TOKEN_HOST,
            access_token=SecretStr(_ACCESS),
            token_id=_TOKEN_ID,
        )
    )

    def unconfirmed(**kwargs: object) -> None:
        raise DurabilityUnconfirmed("the directory could not be synced")

    monkeypatch.setattr(credentials_module, "confirm_credentials_durable", unconfirmed)
    _retry_pending_revocation()
    assert load_pending_revocations().entries, "an unconfirmed write keeps its record"

    monkeypatch.setattr(
        credentials_module, "confirm_credentials_durable", lambda **_kwargs: True
    )
    _retry_pending_revocation()
    assert not load_pending_revocations().entries


def test_a_filesystem_that_cannot_confirm_says_so_and_moves_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ "Cannot confirm" is a third outcome, not a quiet success or a failure.

    Where a directory cannot be synced at all, no amount of retrying will ever
    establish durability — so holding the record forever would occupy a slot
    and achieve nothing. It is dropped, with the weaker guarantee stated rather
    than implied.
    """
    from rememberstack.surfaces import credentials as credentials_module
    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    _isolate_config(monkeypatch, tmp_path)
    write_credentials(credential=_stored())
    _mock_client(monkeypatch, lambda request: httpx.Response(204))
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host=_TOKEN_HOST,
            access_token=SecretStr(_ACCESS),
            token_id=_TOKEN_ID,
        )
    )
    monkeypatch.setattr(
        credentials_module, "confirm_credentials_durable", lambda **_kwargs: False
    )

    _retry_pending_revocation()

    assert "cannot confirm" in capsys.readouterr().err
    assert not load_pending_revocations().entries


def test_an_unsupported_directory_sync_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The three outcomes are distinct at the source, not just at the caller."""
    import errno as errno_module
    import os as os_module

    from rememberstack.surfaces.credentials import confirm_credentials_durable
    from rememberstack.surfaces.credentials import credentials_dir
    from rememberstack.surfaces.credentials import DurabilityUnconfirmed

    _isolate_config(monkeypatch, tmp_path)
    credentials_dir().mkdir(mode=0o700, parents=True, exist_ok=True)

    assert confirm_credentials_durable() is True

    def unsupported(_handle: int) -> None:
        raise OSError(errno_module.EINVAL, "this filesystem cannot sync a directory")

    monkeypatch.setattr(os_module, "fsync", unsupported)
    assert confirm_credentials_durable() is False

    def broken(_handle: int) -> None:
        raise OSError(errno_module.EIO, "the device is on fire")

    monkeypatch.setattr(os_module, "fsync", broken)
    with pytest.raises(DurabilityUnconfirmed):
        confirm_credentials_durable()


def test_a_credential_that_never_parses_is_still_withdrawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 200 means the token host issued something, whatever happened next.

    A body that carries a bearer but fails validation used to escape with that
    credential live and named by nothing: the raw body is the only place its
    secret still exists, so it is the last chance to give it back.
    """
    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            calls.append("revoke")
            return httpx.Response(204)
        if request.url.path == "/v1/device/authorize":
            calls.append("authorize")
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-SECRET",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://tokens.example.test/device",
                    "verification_uri_complete": "https://tokens.example.test/device?c=1",
                    "expires_in": 90,
                    "interval": 1,
                },
            )
        if request.url.path == "/v1/device/token":
            calls.append("token")
            # A bearer, and an expiry the stored model refuses: issued, unusable.
            return httpx.Response(200, json=_token_body(expires_at="not-a-timestamp"))
        return httpx.Response(404)

    _mock_client(monkeypatch, handler)

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 1
    assert "revoke" in calls, "the issued credential was given back"
    assert load_credentials() is None


def test_an_unconfirmable_filesystem_warns_at_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unsupported is not durable, and login says so rather than implying it."""
    from rememberstack.surfaces import credentials as credentials_module
    from rememberstack.surfaces.credentials import write_credentials as real_write

    _isolate_config(monkeypatch, tmp_path)
    calls: list[str] = []
    _mock_client(monkeypatch, _grant_handler(token_body=_token_body(), calls=calls))

    def unconfirmable(**kwargs: object) -> bool:
        real_write(**kwargs)  # type: ignore[arg-type]
        return False

    monkeypatch.setattr(credentials_module, "write_credentials", unconfirmable)

    assert cli_main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    assert "cannot confirm" in capsys.readouterr().err
    assert load_credentials() is not None
    # Dropped, not held: this filesystem can never confirm anything, so keeping
    # the record would occupy a journal slot forever without resolving. The
    # same rule recovery follows.
    from rememberstack.surfaces.credentials import load_pending_revocations

    assert not load_pending_revocations().entries


def test_signals_are_held_process_wide_while_a_credential_is_recorded() -> None:
    """Some sequences cannot be made safe by arranging `try` blocks.

    A signal lands *between* bytecodes, and the gaps between statements belong
    to no exception region — so the window from "the token host answered" to
    "the credential is written down" is closed by deferring the signal.

    Handlers, not a mask: `pthread_sigmask` blocks only the calling thread, so
    a process-directed signal delivered to any other thread would still have
    killed the process mid-record.
    """
    import os as os_module
    import signal as signal_module

    from rememberstack.surfaces.credentials import DeferredInterrupts

    if not hasattr(signal_module, "SIGUSR1"):  # pragma: no cover - Windows
        pytest.skip("this platform has no POSIX signals")

    original = signal_module.getsignal(signal_module.SIGINT)
    try:
        with pytest.raises(KeyboardInterrupt):
            with DeferredInterrupts() as deferred:
                os_module.kill(os_module.getpid(), signal_module.SIGINT)
                # Held: the region completes rather than unwinding here.
                assert deferred is not None
        # And the previous handler is back.
        assert signal_module.getsignal(signal_module.SIGINT) is original
    finally:
        signal_module.signal(signal_module.SIGINT, original)


def test_a_held_signal_is_delivered_as_soon_as_nothing_is_at_stake() -> None:
    """Waiting longer than necessary would only make Ctrl-C feel broken."""
    import os as os_module
    import signal as signal_module

    from rememberstack.surfaces.credentials import DeferredInterrupts

    if not hasattr(signal_module, "SIGUSR1"):  # pragma: no cover - Windows
        pytest.skip("this platform has no POSIX signals")

    original = signal_module.getsignal(signal_module.SIGINT)
    try:
        with pytest.raises(KeyboardInterrupt):
            with DeferredInterrupts() as deferred:
                os_module.kill(os_module.getpid(), signal_module.SIGINT)
                deferred.deliver_if_pending()
                raise AssertionError("the held signal should have arrived")
    finally:
        signal_module.signal(signal_module.SIGINT, original)


def test_the_poll_releases_the_hold_between_attempts() -> None:
    """The sleep is where a user actually waits, so it stays interruptible."""
    import signal as signal_module

    from rememberstack.surfaces.device_login import poll_device_token

    if not hasattr(signal_module, "pthread_sigmask"):  # pragma: no cover
        pytest.skip("this platform has no signal mask")

    masked_during_sleep: list[bool] = []

    def observe(_seconds: float) -> None:
        blocked = signal_module.pthread_sigmask(signal_module.SIG_BLOCK, set())
        masked_during_sleep.append(signal_module.SIGINT in blocked)

    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        polls["n"] += 1
        if polls["n"] == 1:
            return httpx.Response(
                400,
                json={"error": "authorization_pending", "error_description": "wait"},
            )
        return httpx.Response(200, json=_token_body())

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url=_TOKEN_HOST, transport=transport) as client:
        poll_device_token(
            client=client,
            device_code="DEVICE-SECRET",
            interval=0,
            expires_in=5,
            sleep=observe,
        )

    assert masked_during_sleep, "the poll must have slept at least once"
    assert not any(masked_during_sleep), "Ctrl-C works while waiting"


def test_a_previously_installed_handler_is_honoured_not_replaced() -> None:
    """Deferral changes *when* a signal is honoured, not *how*.

    A program that installed its own handler — an application embedding this
    CLI, a test harness — expects that handler to run. Raising in its place
    would take a decision that was never ours.
    """
    import os as os_module
    import signal as signal_module

    from rememberstack.surfaces.credentials import DeferredInterrupts

    if not hasattr(signal_module, "SIGUSR1"):  # pragma: no cover - Windows
        pytest.skip("this platform has no POSIX signals")

    seen: list[int] = []
    original = signal_module.getsignal(signal_module.SIGTERM)
    try:
        signal_module.signal(
            signal_module.SIGTERM, lambda number, _frame: seen.append(number)
        )
        with DeferredInterrupts(signal_module.SIGTERM):
            os_module.kill(os_module.getpid(), signal_module.SIGTERM)
            assert seen == [], "held, not delivered"
        assert seen == [signal_module.SIGTERM], "the caller's handler ran"
    finally:
        signal_module.signal(signal_module.SIGTERM, original)


def test_an_ignored_signal_stays_ignored() -> None:
    """`SIG_IGN` is a decision too, and deferral must not overrule it."""
    import os as os_module
    import signal as signal_module

    from rememberstack.surfaces.credentials import DeferredInterrupts

    if not hasattr(signal_module, "SIGUSR1"):  # pragma: no cover - Windows
        pytest.skip("this platform has no POSIX signals")

    original = signal_module.getsignal(signal_module.SIGTERM)
    try:
        signal_module.signal(signal_module.SIGTERM, signal_module.SIG_IGN)
        with DeferredInterrupts(signal_module.SIGTERM):
            os_module.kill(os_module.getpid(), signal_module.SIGTERM)
        # No SystemExit: the program had already said to ignore this.
    finally:
        signal_module.signal(signal_module.SIGTERM, original)


def test_the_poll_follows_fewer_redirects_than_the_shared_default() -> None:
    """Every redirect the poll follows is another timeout Ctrl-C waits behind."""
    from rememberstack.surfaces.device_login import _MAX_REDIRECTS
    from rememberstack.surfaces.device_login import _POLL_MAX_REDIRECTS

    assert _POLL_MAX_REDIRECTS < _MAX_REDIRECTS


def test_the_poll_follows_one_redirect_rather_than_refusing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget counts redirects, not sends — they differ by the first request.

    Conflating them made a budget of 1 reject the first redirect it saw, which
    is the opposite of following one.
    """
    from rememberstack.surfaces.device_login import request_same_origin

    sends: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sends.append(str(request.url))
        if len(sends) == 1:
            return httpx.Response(
                307, headers={"location": f"{_TOKEN_HOST}/v1/device/token/moved"}
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url=_TOKEN_HOST, transport=transport) as client:
        response = request_same_origin(
            client=client, method="POST", url="/v1/device/token", max_redirects=1
        )

    assert response.status_code == 200
    assert len(sends) == 2, "one redirect is two requests"


def test_a_redirect_budget_of_zero_refuses_the_first_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And zero really means none, so the boundary is not off by one."""
    from rememberstack.surfaces.device_login import DeviceGrantError
    from rememberstack.surfaces.device_login import request_same_origin

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307, headers={"location": f"{_TOKEN_HOST}/v1/device/token/moved"}
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url=_TOKEN_HOST, transport=transport) as client:
        with pytest.raises(DeviceGrantError):
            request_same_origin(
                client=client, method="POST", url="/v1/device/token", max_redirects=0
            )
