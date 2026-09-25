"""Tests for remember.Client facade (D65)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import remember
from remember import Client


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
                "mime": "text/markdown",
                "title": None,
                "versioning_mode": "snapshot",
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
                "mime": "text/markdown",
                "title": None,
                "versioning_mode": "snapshot",
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
    for name in (
        "Client",
        "AccountApiUnavailable",
        "Envelope",
        "ContextBundleV2",
        "IngestedVersion",
        "MemoryApiError",
        "ProjectResolutionError",
        "RateLimited",
        "ReadinessRequirements",
        "StoredKeyRefused",
        "resolve_connection",
    ):
        assert hasattr(remember, name), name
    for removed in ("CloudClient", "ClientSettings", "BillingStatus", "CloudError"):
        assert not hasattr(remember, removed), removed
