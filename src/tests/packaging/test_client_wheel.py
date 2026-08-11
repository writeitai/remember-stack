"""WP-5.7 acceptance: the built base wheel is a usable remote client."""

from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import tomllib
from urllib.parse import parse_qs
from urllib.parse import urlparse
import zipfile

import pytest


def _declared_version() -> str:
    """Read the one authoritative distribution version from pyproject.

    The wheel must report the version the release contract binds across
    pyproject, the Compose image tag and uv.lock — so assert against that
    single source rather than a literal that has to be edited on every
    release and silently rots between them.
    """
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


class _DeploymentHandler(BaseHTTPRequestHandler):
    """Tiny HTTP deployment used from outside the source environment."""

    ingested: list[tuple[dict[str, list[str]], bytes]] = []
    operation_arguments: list[dict[str, object]] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/operations":
            self._json(
                [
                    {
                        "name": "resolve_entity",
                        "description": "Resolve an entity.",
                        "input_schema": {"type": "object"},
                        "result_schema": {"type": "object"},
                        "result_contract": "envelope",
                        "output_grain": "fact",
                        "answer_intent": "identity",
                    }
                ]
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlparse(self.path)
        content = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if parsed.path == "/ingest":
            self.ingested.append((parse_qs(parsed.query), content))
            self._json(
                {
                    "deployment_id": "57000000-0000-0000-0000-000000000001",
                    "doc_id": "57000000-0000-0000-0000-000000000002",
                    "version_id": "57000000-0000-0000-0000-000000000003",
                    "content_hash": "a" * 64,
                    "created": True,
                }
            )
            return
        if parsed.path == "/operations/resolve_entity":
            self.operation_arguments.append(json.loads(content))
            self._json(
                {
                    "grain": "fact",
                    "temporal_scope": {
                        "mode": "current",
                        "evaluated_at": "2026-07-20T10:30:00Z",
                        "believed_at": "2026-07-20T10:30:00Z",
                        "identity_regime": "current",
                    },
                    "freshness": {"pg_live_ts": "2026-07-20T10:30:00Z"},
                }
            )
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the packaging proof's output deterministic."""

    def _json(self, value: object) -> None:
        content = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def test_fresh_base_wheel_queries_and_ingests_over_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build/install in a new venv, then query and push stable-source bytes."""
    project_root = Path(__file__).resolve().parents[3]
    dist = tmp_path / "dist"
    environment = tmp_path / "venv"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("rememberstack-*.whl"))
    _assert_dependency_split(wheel=wheel)
    subprocess.run(
        ["uv", "venv", str(environment)], check=True, capture_output=True, text=True
    )
    python = environment / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "import importlib.util; "
                "from rememberstack.client import MemoryClient, PipelineReadinessReport; "
                "assert MemoryClient.__name__ == 'MemoryClient'; "
                "assert PipelineReadinessReport.__name__ == 'PipelineReadinessReport'; "
                "assert importlib.util.find_spec('sqlalchemy') is None"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    executable = environment / "bin" / "remember"
    source = tmp_path / "fresh-wheel.md"
    source.write_bytes(b"fresh wheel push\n")
    _DeploymentHandler.ingested = []
    _DeploymentHandler.operation_arguments = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DeploymentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(
            "REMEMBERSTACK_API_URL", f"http://127.0.0.1:{server.server_port}"
        )
        version = subprocess.run(
            [str(executable), "--version"], check=True, capture_output=True, text=True
        )
        listing = subprocess.run(
            [str(executable), "operations", "list"],
            check=True,
            capture_output=True,
            text=True,
        )
        query = subprocess.run(
            [
                str(executable),
                "operations",
                "run",
                "resolve_entity",
                "--arg",
                "name=Alice",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        ingest = subprocess.run(
            [
                str(executable),
                "ingest",
                str(source),
                "--source-kind",
                "fresh-wheel",
                "--source-ref",
                "external/note-7",
                "--source-version-ref",
                "r1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert version.stdout.strip() == f"RememberStack {_declared_version()}"
    assert json.loads(listing.stdout)["name"] == "resolve_entity"
    assert json.loads(query.stdout)["grain"] == "fact"
    assert json.loads(ingest.stdout)["created"] is True
    assert _DeploymentHandler.operation_arguments == [{"name": "Alice"}]
    ((parameters, content),) = _DeploymentHandler.ingested
    assert parameters["source_kind"] == ["fresh-wheel"]
    assert parameters["source_ref"] == ["external/note-7"]
    assert parameters["source_version_ref"] == ["r1"]
    assert content == b"fresh wheel push\n"


def _assert_dependency_split(*, wheel: Path) -> None:
    """The base requires client libraries; heavy runtime lives in extras."""
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
    requirements = metadata.get_all("Requires-Dist", [])
    base = [
        requirement for requirement in requirements if "extra ==" not in requirement
    ]
    assert {requirement.split(">=")[0] for requirement in base} == {
        "httpx",
        "pydantic",
        "pydantic-settings",
    }
    assert set(metadata.get_all("Provides-Extra", [])) == {
        "benchmark",
        "connectors-watched-directory",
        "k",
        "observability",
        "server",
    }
    assert any(requirement.startswith("sqlalchemy") for requirement in requirements)
