"""Tests for remember.Client facade (D65)."""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import SecretStr
import pytest

import remember
from remember import Client
from remember.client import _format_bearer
from rememberstack.client import ClientSettings


def test_format_bearer() -> None:
    assert _format_bearer("umc_dp_secret") == "Bearer umc_dp_secret"
    assert _format_bearer("Bearer umc_dp_secret") == "Bearer umc_dp_secret"
    assert _format_bearer("bearer umc_dp_secret") == "Bearer umc_dp_secret"
    assert _format_bearer("  Bearer  umc_dp_secret  ") == "Bearer umc_dp_secret"


def test_format_bearer_validation() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        _format_bearer("")
    with pytest.raises(ValueError, match="cannot be empty"):
        _format_bearer("   ")
    with pytest.raises(ValueError, match="cannot be empty"):
        _format_bearer("Bearer")
    with pytest.raises(ValueError, match="cannot be empty"):
        _format_bearer("Bearer   ")
    with pytest.raises(ValueError, match="newline"):
        _format_bearer("umc_dp_key\r\n")
    with pytest.raises(ValueError, match="newline"):
        _format_bearer("Bearer secret\n")


def test_client_bare_api_key_formats_bearer() -> None:
    client = Client(api_key="umc_dp_12345", base_url="https://dp.test")
    auth_header = client._client.headers.get("Authorization")
    assert auth_header == "Bearer umc_dp_12345"
    assert str(client._client.base_url) == "https://dp.test"


def test_client_api_key_with_bearer_prefix() -> None:
    client = Client(api_key="Bearer umc_dp_12345", base_url="https://dp.test")
    auth_header = client._client.headers.get("Authorization")
    assert auth_header == "Bearer umc_dp_12345"


def test_client_api_key_takes_precedence_over_authorization() -> None:
    client = Client(
        api_key="umc_dp_primary",
        authorization="Bearer umc_dp_fallback",
        base_url="https://dp.test",
    )
    assert client._client.headers.get("Authorization") == "Bearer umc_dp_primary"


def test_client_env_var_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEMBER_API_KEY", "umc_dp_from_env")
    monkeypatch.setenv("REMEMBER_API_URL", "https://env.dp.remember.dev")
    # Fallbacks that must be overridden
    monkeypatch.setenv("REMEMBERSTACK_API_AUTHORIZATION", "Bearer rs_auth")
    monkeypatch.setenv("REMEMBERSTACK_API_URL", "https://rs.dp.remember.dev")

    client = Client.from_env()
    assert client._client.headers.get("Authorization") == "Bearer umc_dp_from_env"
    assert str(client._client.base_url) == "https://env.dp.remember.dev"


def test_client_falls_back_to_rememberstack_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REMEMBER_API_KEY", raising=False)
    monkeypatch.delenv("REMEMBER_API_URL", raising=False)
    monkeypatch.setenv("REMEMBERSTACK_API_AUTHORIZATION", "Bearer rs_auth")
    monkeypatch.setenv("REMEMBERSTACK_API_URL", "https://rs.dp.remember.dev")

    client = Client.from_env()
    assert client._client.headers.get("Authorization") == "Bearer rs_auth"
    assert str(client._client.base_url) == "https://rs.dp.remember.dev"


def test_client_explicit_settings_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REMEMBER_API_KEY", "umc_dp_ambient")
    monkeypatch.setenv("REMEMBER_API_URL", "https://ambient.dp.remember.dev")

    settings = ClientSettings(
        api_url="https://settings.dp.remember.dev",
        api_authorization=SecretStr("Bearer rs_settings_auth"),
    )
    client = Client(settings=settings)
    assert client._client.headers.get("Authorization") == "Bearer rs_settings_auth"
    assert str(client._client.base_url) == "https://settings.dp.remember.dev"


def test_client_ignores_cloud_token_and_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMEMBER_API_KEY", raising=False)
    monkeypatch.delenv("REMEMBER_API_URL", raising=False)
    monkeypatch.delenv("REMEMBERSTACK_API_AUTHORIZATION", raising=False)
    monkeypatch.delenv("REMEMBERSTACK_API_URL", raising=False)
    monkeypatch.setenv("REMEMBER_CLOUD_TOKEN", "umc_cp_secret")
    monkeypatch.setenv("REMEMBER_CLOUD_ORG", "some-org-id")

    # Client must NOT pick up control-plane tokens
    client = Client.from_env()
    assert client._client.headers.get("Authorization") is None


def test_client_injected_client_refuses_settings() -> None:
    mock_http = httpx.Client()
    with pytest.raises(ValueError, match="injected client cannot be combined"):
        Client(client=mock_http, api_key="umc_dp_test")


def test_client_ingest_string_path(tmp_path: Path) -> None:
    test_file = tmp_path / "sample.txt"
    test_file.write_text("hello memory")

    seen_request: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_request["path"] = request.url.path
        seen_request["filename"] = request.url.params.get("filename", "")
        seen_request["mime"] = request.url.params.get("mime", "")
        seen_request["content"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "deployment_id": "00000000-0000-0000-0000-000000000000",
                "doc_id": "11111111-1111-1111-1111-111111111111",
                "version_id": "22222222-2222-2222-2222-222222222222",
                "content_hash": "sha256:abcd",
                "created": True,
            },
        )

    mock_client = httpx.Client(
        base_url="https://dp.test", transport=httpx.MockTransport(handler)
    )
    with Client(client=mock_client) as memory:
        landed = memory.ingest(str(test_file))

    assert seen_request["path"] == "/ingest"
    assert seen_request["filename"] == "sample.txt"
    assert seen_request["mime"] == "text/plain"
    assert seen_request["content"] == "hello memory"
    assert str(landed.version_id) == "22222222-2222-2222-2222-222222222222"
    assert landed.created is True


def test_client_ingest_file_alias(tmp_path: Path) -> None:
    test_file = tmp_path / "doc.md"
    test_file.write_text("# Doc\n\nContent here.")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "deployment_id": "00000000-0000-0000-0000-000000000000",
                "doc_id": "11111111-1111-1111-1111-111111111111",
                "version_id": "22222222-2222-2222-2222-222222222222",
                "content_hash": "sha256:abcd",
                "created": True,
            },
        )

    mock_client = httpx.Client(
        base_url="https://dp.test", transport=httpx.MockTransport(handler)
    )
    with Client(client=mock_client) as memory:
        # String path
        landed_str = memory.ingest_file(str(test_file))
        assert landed_str.created is True

        # Path object
        landed_path = memory.ingest_file(test_file)
        assert landed_path.created is True


def test_top_level_exports() -> None:
    assert hasattr(remember, "Client")
    assert hasattr(remember, "CloudClient")
    assert hasattr(remember, "Envelope")
    assert hasattr(remember, "ContextBundleV2")
    assert hasattr(remember, "IngestedVersion")
    assert hasattr(remember, "MemoryApiError")
    assert hasattr(remember, "ReadinessRequirements")
    assert hasattr(remember, "BillingStatus")
    assert hasattr(remember, "Deployment")
    assert hasattr(remember, "LedgerEntry")
    assert hasattr(remember, "SpendGate")
