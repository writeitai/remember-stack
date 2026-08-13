"""D92 ``remember login`` / ``logout`` — CLI only, no SDK file pickup."""

from __future__ import annotations

import json
from pathlib import Path
import stat
from uuid import UUID

import httpx
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


def test_second_login_aborts_when_revoke_is_5xx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A live token must not be replaced when revoke cannot be confirmed."""
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
    assert (
        _retry_after_seconds(response=response, fallback=10.0, bump=True) == 15.0
    )
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
