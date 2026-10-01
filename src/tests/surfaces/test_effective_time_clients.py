"""D140 through the SDK, the CLI and both MCP servers.

Every client path ends at the same HTTP routes (or, for the in-process MCP
server, the same E0 port). These tests drive the real HTTP app over recording
ports from each surface: what is sent, what is refused before any request,
and how the engine's refusals come back.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient
import httpx
import pytest

from remember import EffectivePeriodInput
from remember import EffectivePeriodsSet
from remember import EffectiveTimeCleared
from remember import MemoryApiError
from remember import MemoryClient
from remember.cli import main as cli_main
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import INGEST_TOOL_NAME
from remember.mcp_tools import memory_tools
from remember.mcp_tools import tool
from remember.models import DeclaredEffectivePeriod
from rememberstack.model import DocumentUpload
from rememberstack.model import EffectivePeriodConflictError
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import VersionKeyConflictError
from rememberstack.surfaces import build_api
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.mcp import OperationMcpServer

_DEPLOYMENT_ID = UUID("14100000-0000-0000-0000-000000000001")
_DOC = UUID("14100000-0000-0000-0000-00000000000d")
_VERSION = UUID("14100000-0000-0000-0000-00000000000e")
_OTHER = UUID("14100000-0000-0000-0000-00000000000f")
_JAN = datetime(2026, 1, 1, tzinfo=UTC)
_JUL = datetime(2026, 7, 1, tzinfo=UTC)


class _Boundary:
    """Open admission and readiness for the one deployment under test."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        assert deployment_id == _DEPLOYMENT_ID


class _Ingest:
    """Record observed uploads; refuse the key ``taken``."""

    def __init__(self) -> None:
        self.uploads: list[DocumentUpload] = []

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        raise AssertionError("D140 fields never take the anonymous path")

    def ingest_observed(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        upload: DocumentUpload,
        versioning_mode: str,
        source_modified_at: datetime | None,
        source_version_ref: str | None,
        sync_cycle_id: UUID | None,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        assert deployment_id == _DEPLOYMENT_ID
        self.uploads.append(upload)
        if upload.version_key == "taken":
            raise VersionKeyConflictError(version_key="taken", version_id=_OTHER)
        return IngestedVersion(
            deployment_id=deployment_id,
            doc_id=_DOC,
            version_id=_VERSION,
            content_hash="h",
            created=True,
            mime=upload.mime,
            title="t",
            versioning_mode="snapshot",
            version_key=upload.version_key,
        )


class _Readiness:
    """Readiness is never asked in these tests."""

    def inspect(self, **_: object) -> object:
        raise AssertionError("readiness is not part of these proofs")


class _EffectiveTime:
    """Echo a replacement set; refuse a start taken by ``_OTHER``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def set_effective_periods(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        periods: tuple[EffectivePeriodInput, ...],
    ) -> EffectivePeriodsSet:
        assert deployment_id == _DEPLOYMENT_ID
        self.calls.append(("set", periods))
        if any(period.effective_from == _JUL for period in periods):
            raise EffectivePeriodConflictError(effective_from=_JUL, version_id=_OTHER)
        return EffectivePeriodsSet(
            doc_id=doc_id,
            version_id=version_id,
            periods=tuple(
                DeclaredEffectivePeriod(
                    period_id=_OTHER,
                    effective_from=period.effective_from,
                    effective_until=period.effective_until,
                    declared_at=_JUL,
                )
                for period in periods
            ),
            declared=len(periods),
            retracted=0,
        )

    def clear_effective_time(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> EffectiveTimeCleared:
        assert deployment_id == _DEPLOYMENT_ID
        self.calls.append(("clear", doc_id))
        return EffectiveTimeCleared(doc_id=doc_id, retracted=2, cleared_at=_JUL)


@pytest.fixture()
def surface() -> tuple[MemoryClient, _Ingest, _EffectiveTime]:
    """The real HTTP app with only ingest and the period routes composed."""
    ingest = _Ingest()
    effective_time = _EffectiveTime()
    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        ingest=ingest,
        effective_time=effective_time,
    )
    return MemoryClient(client=TestClient(app)), ingest, effective_time


def _no_requests() -> MemoryClient:
    """A client whose transport fails the test on any request."""

    def respond(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    return MemoryClient(
        client=httpx.Client(
            base_url="http://memory.test", transport=httpx.MockTransport(respond)
        )
    )


def test_sdk_ingest_sends_the_version_key_and_period(
    surface: tuple[MemoryClient, _Ingest, _EffectiveTime],
) -> None:
    """The three D140 arguments arrive on the upload; the key comes back."""
    client, ingest, _ = surface

    receipt = client.ingest(
        content=b"# Travel",
        filename="travel.md",
        source_kind="intranet",
        source_ref="policy/travel",
        version_key="edition-2",
        effective_from=_JAN,
        effective_until=_JUL,
    )

    assert receipt.version_key == "edition-2"
    upload = ingest.uploads[0]
    assert (upload.version_key, upload.effective_from, upload.effective_until) == (
        "edition-2",
        _JAN,
        _JUL,
    )


def test_sdk_reports_a_taken_version_key_as_a_409(
    surface: tuple[MemoryClient, _Ingest, _EffectiveTime],
) -> None:
    """The conflict names the version that owns the key."""
    client, _, _ = surface

    with pytest.raises(MemoryApiError) as refused:
        client.ingest(
            content=b"# Travel",
            filename="travel.md",
            source_kind="intranet",
            source_ref="policy/travel",
            version_key="taken",
        )
    assert refused.value.status_code == 409
    assert str(_OTHER) in str(refused.value.detail)


@pytest.mark.parametrize(
    "arguments",
    [
        {"version_key": "edition-1"},
        {"effective_from": _JAN},
        {
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "effective_until": _JUL,
        },
        {
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "effective_from": _JUL,
            "effective_until": _JAN,
        },
        {
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "effective_from": datetime(2026, 1, 1),
        },
        {
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "effective_from": _JAN,
            "versioning_mode": "living",
        },
    ],
)
def test_sdk_refuses_malformed_d140_ingests_before_any_request(
    arguments: dict[str, object],
) -> None:
    """No lineage, an end without an earlier start, naive or living: ValueError."""
    with pytest.raises(ValueError):
        _no_requests().ingest(content=b"x", filename="x.md", **arguments)  # type: ignore[arg-type]


def test_sdk_sets_and_clears_effective_periods(
    surface: tuple[MemoryClient, _Ingest, _EffectiveTime],
) -> None:
    """Typed results from both routes; string ids are accepted and validated."""
    client, _, effective_time = surface

    result = client.set_effective_periods(
        doc_id=str(_DOC),
        version_id=_VERSION,
        periods=[EffectivePeriodInput(effective_from=_JAN)],
    )
    cleared = client.clear_effective_time(doc_id=_DOC)

    assert isinstance(result, EffectivePeriodsSet)
    assert (result.doc_id, result.version_id, result.declared) == (_DOC, _VERSION, 1)
    assert result.periods[0].effective_from == _JAN
    assert isinstance(cleared, EffectiveTimeCleared)
    assert (cleared.retracted, cleared.cleared_at) == (2, _JUL)
    assert [kind for kind, _ in effective_time.calls] == ["set", "clear"]


def test_sdk_reports_a_taken_start_as_a_409(
    surface: tuple[MemoryClient, _Ingest, _EffectiveTime],
) -> None:
    """A start declared for another version is refused with that version named."""
    client, _, _ = surface

    with pytest.raises(MemoryApiError) as refused:
        client.set_effective_periods(
            doc_id=_DOC,
            version_id=_VERSION,
            periods=[EffectivePeriodInput(effective_from=_JUL)],
        )
    assert refused.value.status_code == 409


def test_sdk_refuses_malformed_ids_before_any_request() -> None:
    """An id that is no UUID can never become a path segment."""
    client = _no_requests()
    with pytest.raises(ValueError):
        client.clear_effective_time(doc_id="../operations")
    with pytest.raises(ValueError):
        client.set_effective_periods(doc_id=_DOC, version_id="x", periods=[])


def test_cli_ingest_forwards_the_d140_flags(
    surface: tuple[MemoryClient, _Ingest, _EffectiveTime],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`remember ingest --version-key --effective-from --effective-until`."""
    client, ingest, _ = surface
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)
    document = tmp_path / "travel.md"
    document.write_text("# Travel", encoding="utf-8")

    assert (
        cli_main(
            [
                "ingest",
                str(document),
                "--source-kind",
                "intranet",
                "--source-ref",
                "policy/travel",
                "--version-key",
                "edition-1",
                "--effective-from",
                "2026-01-01T00:00:00+00:00",
                "--effective-until",
                "2026-07-01T00:00:00+00:00",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["version_key"] == "edition-1"
    upload = ingest.uploads[0]
    assert (upload.effective_from, upload.effective_until) == (_JAN, _JUL)


def _payload(result: dict[str, object]) -> dict[str, object]:
    """Decode the single JSON text block of an MCP tool result."""
    content = cast("list[dict[str, str]]", result["content"])
    decoded = json.loads(content[0]["text"])
    return decoded["error"] if result["isError"] else decoded


def test_ingest_tool_publishes_the_d140_arguments_at_version_two() -> None:
    """A changed input schema is a new tool version (D136)."""
    definition = tool(INGEST_TOOL_NAME)
    properties = cast("dict[str, object]", definition.input_schema["properties"])

    assert definition.tool_version == 2
    assert {"version_key", "effective_from", "effective_until"} <= set(properties)
    assert {item.name: item.tool_version for item in memory_tools()}[
        INGEST_TOOL_NAME
    ] == 2


def _remote(client: MemoryClient) -> EngineMcpServer:
    return EngineMcpServer(client=client, read_only=False, path_ingest=False)


def test_remember_mcp_ingest_forwards_the_d140_arguments(
    surface: tuple[MemoryClient, _Ingest, _EffectiveTime],
) -> None:
    """The remote tool carries the key and period through the SDK."""
    client, ingest, _ = surface

    result = _remote(client).call_tool(
        name=INGEST_TOOL_NAME,
        arguments={
            "text": "# Travel",
            "filename": "travel.md",
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "version_key": "edition-2",
            "effective_from": "2026-01-01T00:00:00Z",
            "effective_until": "2026-07-01T00:00:00Z",
        },
    )

    assert result["isError"] is False, result
    upload = ingest.uploads[0]
    assert (upload.version_key, upload.effective_from, upload.effective_until) == (
        "edition-2",
        _JAN,
        _JUL,
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"version_key": "edition-1"},
        {
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "effective_until": "2026-07-01T00:00:00Z",
        },
        {
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "effective_from": "2026-01-01T00:00:00",
        },
        {
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "effective_from": "2026-01-01T00:00:00Z",
            "versioning_mode": "living",
        },
    ],
)
def test_remember_mcp_refuses_malformed_d140_arguments(
    surface: tuple[MemoryClient, _Ingest, _EffectiveTime], extra: dict[str, str]
) -> None:
    """Refused as arguments; nothing reaches the engine."""
    client, ingest, _ = surface

    result = _remote(client).call_tool(
        name=INGEST_TOOL_NAME,
        arguments={"text": "# Travel", "filename": "travel.md", **extra},
    )

    assert result["isError"] is True
    assert _payload(result)["code"] in {"invalid_arguments", "source_lineage_pair"}
    assert ingest.uploads == []


class _StubSurface:
    """An operation surface with no operations, for the local MCP server."""

    deployment_id = _DEPLOYMENT_ID

    def descriptors(self) -> tuple[object, ...]:
        return ()


def test_local_mcp_maps_a_taken_key_like_the_http_409() -> None:
    """The in-process backend raises the engine's typed error; the tool maps it."""
    ingest = _Ingest()
    server = OperationMcpServer(
        surface=_StubSurface(),  # type: ignore[arg-type]
        ingest=ingest,
        pipeline_readiness=_Readiness(),  # type: ignore[arg-type]
    )

    accepted = server.call_tool(
        name=INGEST_TOOL_NAME,
        arguments={
            "text": "# Travel",
            "filename": "travel.md",
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "version_key": "edition-1",
            "effective_from": "2026-01-01T00:00:00Z",
        },
    )
    refused = server.call_tool(
        name=INGEST_TOOL_NAME,
        arguments={
            "text": "# Travel",
            "filename": "travel.md",
            "source_kind": "intranet",
            "source_ref": "policy/travel",
            "version_key": "taken",
        },
    )

    assert accepted["isError"] is False, accepted
    assert ingest.uploads[0].effective_from == _JAN
    error = _payload(refused)
    assert (error["code"], error["status_code"]) == ("version_key_conflict", 409)
