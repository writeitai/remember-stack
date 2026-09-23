"""First-run client defaults: upload MIME, readiness waiting, control-plane URL."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
import json
import mimetypes
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from remember import MemoryClient
from remember import PipelineDeadLettered
from remember import PipelineReadinessReport
import remember.client as client_module
from remember.credentials import ControlPlaneCredentials
from remember.credentials import CredentialFile
from remember.credentials import DEFAULT_CONTROL_PLANE_URL
from remember.mcp_memory_tools import handle_memory_write_tool
from remember.mcp_memory_tools import McpMemorySettings
from remember.mime import infer_upload_mime
from remember.mime import KNOWN_UPLOAD_MIME_TYPES
from remember.models import IngestedVersion
from remember.models import ReadinessRequirements

_DEPLOYMENT = UUID("58000000-0000-0000-0000-000000000001")
_DOC = UUID("58000000-0000-0000-0000-000000000002")
_VERSION = UUID("58000000-0000-0000-0000-000000000003")
_OOXML = "application/vnd.openxmlformats-officedocument"


@pytest.fixture()
def no_host_mime_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a Python build whose MIME database knows no extension."""
    monkeypatch.setattr(mimetypes, "guess_type", lambda *_a, **_k: (None, None))


# --- G25: deterministic upload MIME ----------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("notes.md", "text/markdown"),
        ("NOTES.MD", "text/markdown"),
        ("notes.markdown", "text/markdown"),
        ("notes.txt", "text/plain"),
        ("page.html", "text/html"),
        ("page.htm", "text/html"),
        ("paper.pdf", "application/pdf"),
        ("scan.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("memo.docx", f"{_OOXML}.wordprocessingml.document"),
        ("deck.pptx", f"{_OOXML}.presentationml.presentation"),
        ("sheet.xlsx", f"{_OOXML}.spreadsheetml.sheet"),
    ],
)
def test_engine_formats_do_not_depend_on_the_host_database(
    no_host_mime_database: None, name: str, expected: str
) -> None:
    """Every engine-handled format maps identically on every Python build."""
    assert infer_upload_mime(name) == expected


def test_unknown_extensions_fall_back_to_the_host_database() -> None:
    """Formats outside the engine table still use the host MIME database."""
    assert infer_upload_mime("data.json") == "application/json"
    assert infer_upload_mime("blob.unknownext") is None
    assert ".md" in KNOWN_UPLOAD_MIME_TYPES


class _IngestRecorder:
    """Mock data plane that records every ingest's query parameters."""

    def __init__(self) -> None:
        self.params: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ingest"
        self.params.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "deployment_id": str(_DEPLOYMENT),
                "doc_id": str(_DOC),
                "version_id": str(_VERSION),
                "content_hash": "0" * 64,
                "created": True,
            },
        )


@pytest.fixture()
def recorded_client() -> Iterator[tuple[MemoryClient, _IngestRecorder]]:
    recorder = _IngestRecorder()
    http = httpx.Client(
        base_url="http://engine.test", transport=httpx.MockTransport(recorder)
    )
    yield MemoryClient(client=http), recorder
    http.close()


def test_sdk_sends_markdown_when_the_host_database_does_not_know_md(
    no_host_mime_database: None,
    recorded_client: tuple[MemoryClient, _IngestRecorder],
    tmp_path: Path,
) -> None:
    """The reported failure: a .md path on python.org 3.12 was octet-stream."""
    client, recorder = recorded_client
    source = tmp_path / "standup.md"
    source.write_text("# Standup", encoding="utf-8")
    client.ingest(source)
    client.ingest(str(source))
    assert [p["mime"] for p in recorder.params] == ["text/markdown"] * 2


def test_sdk_infers_bytes_mime_from_the_filename(
    recorded_client: tuple[MemoryClient, _IngestRecorder],
) -> None:
    """Bytes take the type of the filename they are sent under."""
    client, recorder = recorded_client
    client.ingest(content=b"%PDF-1.7", filename="paper.pdf")
    client.ingest(b"# note", filename="note.md")
    client.ingest(content=b"\x00", filename="blob.unknownext")
    assert [p["mime"] for p in recorder.params] == [
        "application/pdf",
        "text/markdown",
        "application/octet-stream",
    ]


def test_sdk_explicit_mime_always_wins(
    recorded_client: tuple[MemoryClient, _IngestRecorder], tmp_path: Path
) -> None:
    """An explicit mime overrides inference in path and bytes modes."""
    client, recorder = recorded_client
    source = tmp_path / "notes.md"
    source.write_text("plain", encoding="utf-8")
    client.ingest(source, mime="text/plain")
    client.ingest(content=b"x", filename="notes.md", mime="text/plain")
    assert [p["mime"] for p in recorder.params] == ["text/plain"] * 2


def test_sdk_path_mime_follows_the_real_path_not_the_override(
    recorded_client: tuple[MemoryClient, _IngestRecorder], tmp_path: Path
) -> None:
    """Renaming the upload does not change how the real file is read."""
    client, recorder = recorded_client
    source = tmp_path / "report.md"
    source.write_text("# hi", encoding="utf-8")
    client.ingest(source, filename="report.pdf")
    assert recorder.params[0]["filename"] == "report.pdf"
    assert recorder.params[0]["mime"] == "text/markdown"


def test_cli_ingest_sends_markdown_without_mime_flag(
    no_host_mime_database: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`remember ingest notes.md` needs no --mime on any Python build."""
    from remember.cli import main

    recorder = _IngestRecorder()
    real_client = httpx.Client

    def mock_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(recorder)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "config"))
    source = tmp_path / "standup.md"
    source.write_text("# Standup", encoding="utf-8")
    code = main(["ingest", str(source), "--api-url", "http://127.0.0.1:8000"])
    assert code == 0, capsys.readouterr().err
    assert recorder.params[0]["mime"] == "text/markdown"


class _McpBackend:
    def __init__(self) -> None:
        self.mimes: list[str] = []

    def ingest(self, *, mime: str, **_kwargs: object) -> IngestedVersion:
        self.mimes.append(mime)
        return IngestedVersion(
            deployment_id=_DEPLOYMENT,
            doc_id=_DOC,
            version_id=_VERSION,
            content_hash="0" * 64,
            created=True,
        )

    def pipeline_readiness(
        self, *, version_ids: tuple[UUID, ...], require: ReadinessRequirements
    ) -> PipelineReadinessReport:
        raise AssertionError("not called")

    def max_ingest_body_bytes(self) -> int | None:
        return None


def test_mcp_ingest_infers_mime_in_path_and_filename_modes(
    no_host_mime_database: None, tmp_path: Path
) -> None:
    """MCP path, content_base64 and text modes agree with the SDK."""
    source = tmp_path / "standup.md"
    source.write_text("# Standup", encoding="utf-8")
    backend = _McpBackend()
    settings = McpMemorySettings(ingest_roots=(tmp_path,))
    encoded = base64.b64encode(b"%PDF").decode("ascii")
    calls: list[dict[str, object]] = [
        {"path": str(source)},
        {"content_base64": encoded, "filename": "paper.pdf"},
        {"content_base64": encoded, "filename": "notes.md"},
        {"text": "# hi", "filename": "notes.md"},
        {"text": "hi", "filename": "notes.pdf"},
        {"text": "hi", "filename": "notes.md", "mime": "text/plain"},
    ]
    for arguments in calls:
        result = handle_memory_write_tool(
            name="ingest", arguments=arguments, backend=backend, settings=settings
        )
        assert result["isError"] is False, result
    assert backend.mimes == [
        "text/markdown",
        "application/pdf",
        "text/markdown",
        "text/markdown",
        "text/plain",
        "text/plain",
    ]


# --- G2 + G55: wait_for_readiness -------------------------------------------


def _report(*statuses: str) -> dict[str, object]:
    now = datetime.now(tz=UTC).isoformat()
    ready = all(status == "succeeded" for status in statuses)
    return {
        "ready": ready,
        "versions": [
            {
                "version_id": str(_VERSION),
                "ready": ready,
                "stages": [
                    {
                        "stage": f"stage_{index}",
                        "component_version": "v1",
                        "status": status,
                        "finished_at": now if status == "succeeded" else None,
                    }
                    for index, status in enumerate(statuses)
                ],
            }
        ],
        "capabilities": {
            name: {
                "required": name != "p3",
                "ready": ready,
                "checked_at": now,
                "reason": "ok" if ready else "stage_incomplete",
            }
            for name in ("pipeline", "p1", "live_graph", "p3")
        },
    }


def _readiness_client(
    reports: list[dict[str, object]],
) -> tuple[MemoryClient, list[dict[str, object]]]:
    bodies: list[dict[str, object]] = []
    queue = list(reports)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/readiness"
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=queue.pop(0))

    http = httpx.Client(
        base_url="http://engine.test", transport=httpx.MockTransport(handler)
    )
    return MemoryClient(client=http), bodies


@pytest.fixture()
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(client_module.time, "sleep", recorded.append)
    return recorded


def test_wait_defaults_fit_a_real_pipeline_run() -> None:
    """Defaults are minutes-scale starting points, not seconds."""
    import inspect

    params = inspect.signature(MemoryClient.wait_for_readiness).parameters
    assert params["timeout"].default == 1800.0
    assert params["poll_interval"].default == 15.0


def test_wait_keeps_polling_through_failed_and_returns_ready(
    sleeps: list[float],
) -> None:
    """`failed` has a retry scheduled, so it is not a reason to stop."""
    client, bodies = _readiness_client(
        [
            _report("succeeded", "running"),
            _report("succeeded", "failed"),
            _report("succeeded", "succeeded"),
        ]
    )
    report = client.wait_for_readiness([str(_VERSION)])
    assert report.ready
    assert len(bodies) == 3
    assert sleeps == [15.0, 15.0]
    assert bodies[0]["require"] == {
        "pipeline": True,
        "p1": True,
        "live_graph": True,
        "p3": False,
    }


def test_wait_stops_at_once_on_dead_letter(sleeps: list[float]) -> None:
    """A dead-lettered stage raises a typed error naming version and stage."""
    client, bodies = _readiness_client(
        [_report("succeeded", "running"), _report("succeeded", "dead_letter")]
    )
    with pytest.raises(PipelineDeadLettered) as caught:
        client.wait_for_readiness([_VERSION])
    assert len(bodies) == 2
    assert caught.value.dead_lettered == ((_VERSION, "stage_1", "dead_letter"),)
    assert not caught.value.report.ready
    message = str(caught.value)
    assert str(_VERSION) in message and "stage_1" in message
    assert "dead_letter" in message


def test_wait_times_out_without_overshooting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The last sleep is clipped to the time left before the deadline."""
    clock = {"now": 0.0}
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock["now"])

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr(client_module.time, "sleep", fake_sleep)
    client, bodies = _readiness_client([_report("running")] * 4)
    with pytest.raises(TimeoutError, match="not ready after 40"):
        client.wait_for_readiness([_VERSION], timeout=40, poll_interval=15)
    assert len(bodies) == 4
    assert clock["now"] == 40.0


# --- G12: the control-plane default resolves -------------------------------


def test_every_control_plane_default_is_the_app_api() -> None:
    """One constant; the old api.remember.dev host has no DNS record."""
    assert DEFAULT_CONTROL_PLANE_URL == "https://remember.dev/app/api"
    assert CredentialFile(version=1).token_host == DEFAULT_CONTROL_PLANE_URL
    from pydantic import SecretStr

    control = ControlPlaneCredentials(access_token=SecretStr("umc_cp_x"))
    assert control.url == DEFAULT_CONTROL_PLANE_URL


def test_cloud_client_defaults_to_the_app_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """CloudClient talks to the same control-plane base by default."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    monkeypatch.delenv("REMEMBER_CLOUD_URL", raising=False)
    cloud = client_module.CloudClient(
        token="umc_cp_x", org_id="org", transport=httpx.MockTransport(handler)
    )
    assert cloud.deployments() == []
    assert seen == ["https://remember.dev/app/api/v1/orgs/org/deployments"]


def test_login_resolves_the_app_api_without_token_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`remember login` needs no --token-host for remember.dev."""
    from remember.cli import _resolved_token_host

    for name in (
        "REMEMBER_CONTROL_PLANE_URL",
        "REMEMBER_TOKEN_HOST",
        "REMEMBERSTACK_TOKEN_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    assert _resolved_token_host(explicit=None) == "https://remember.dev/app/api"
