"""Capture the pre-D143 default surfaces of an unconfigured deployment (D143 §8).

One function, :func:`capture_defaults`, renders every database-free default
the D143 compatibility clause freezes: the serialized ``GET /operations`` list
and ``GET /deployment`` body (tools map included) of a fully composed API, the
in-process MCP tool list and the ``remember-mcp`` engine-mode tool lists with
their input schemas, the readiness expected stages with component versions,
and the query-surface manifest hashes.

It runs unchanged against the pre-D143 base and the current branch: optional
D143 ports are composed only when the code accepts them, and then report an
unconfigured deployment (every layer ``enabled``). The checked-in fixture
``src/tests/surfaces/fixtures/d143_defaults.json`` is this function's output,
originally from the pre-D143 base (fbcd489bd) and refreshed for later accepted
baseline moves (p9_42/p9_45/D153 views, D154 pagination, D156 document tags:
the tag port and its five tools, ``ingest``'s ``tags`` argument at
``tool_version`` 3 and the two tag views); ``test_d143_defaults_unchanged.py``
recomputes it.
Run as a script to print the JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from fastapi.testclient import TestClient

_DEPLOYMENT = UUID("d1430000-0000-0000-0000-0000000def01")


class _OpenBoundary:
    """Readiness and admission that let every request through."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[()]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


class _UnconfiguredLayers:
    """What ``ProcessingLayerCatalog.states`` reports with no configuration row."""

    def __init__(self, *, claims: bool = True, facts: bool = True) -> None:
        self.claims = claims
        self.facts = facts

    def states(self, *, deployment_id: UUID) -> object:
        from remember.models import ProcessingLayerStates  # type: ignore[attr-defined]

        return ProcessingLayerStates(
            claims="enabled" if self.claims else "disabled",
            facts="enabled" if self.facts else "disabled",
        )


def _accepts(callable_: Any, name: str) -> bool:
    return name in inspect.signature(callable_).parameters


def _surface() -> Any:
    """An operation surface whose registry holds the canonical operations."""
    from rememberstack.spine.assured_operations import CANONICAL_OPERATIONS
    from rememberstack.surfaces.operation_surface import OperationSurface

    registry = MagicMock()
    registry.active.return_value = CANONICAL_OPERATIONS
    return OperationSurface(
        registry=registry, executor=MagicMock(), deployment_id=_DEPLOYMENT
    )


def _ports() -> dict[str, Any]:
    """Every optional port the self-host profile composes, as stand-ins."""
    surface = _surface()
    open_query = MagicMock()
    open_query.deployment_id = _DEPLOYMENT
    return {
        "surface": surface,
        "open_query": open_query,
        "ingest": MagicMock(),
        "pipeline_readiness": MagicMock(),
        "deletion": MagicMock(),
        "document_search": MagicMock(),
        "section_history": MagicMock(),
        "document_references": MagicMock(),
        "tags": MagicMock(),
    }


def _http(
    ports: dict[str, Any], *, claims: bool = True, facts: bool = True
) -> tuple[Any, Any]:
    """``GET /operations`` and ``GET /deployment`` of a fully composed API."""
    from rememberstack.model import DeploymentBuildInfo
    from rememberstack.surfaces.http_api import build_api

    boundary = _OpenBoundary()
    build_info = MagicMock()
    build_info.build_info.return_value = DeploymentBuildInfo(
        build_revision="d143-defaults", model_bindings={"embedding": "fixture"}
    )
    extra: dict[str, Any] = {}
    if _accepts(build_api, "processing_layers"):
        extra["processing_layers"] = _UnconfiguredLayers(claims=claims, facts=facts)
    app = build_api(
        engine=MagicMock(),
        deployment_id=_DEPLOYMENT,
        admission=boundary,
        readiness=boundary,
        build_info=build_info,
        **ports,
        **extra,
    )
    client = TestClient(app)
    operations = client.get("/operations")
    deployment = client.get("/deployment")
    assert operations.status_code == 200, operations.text
    assert deployment.status_code == 200, deployment.text
    return operations.json(), deployment.json()


def _in_process_mcp(
    ports: dict[str, Any], *, claims: bool = True, facts: bool = True
) -> Any:
    """The engine's own MCP server (``rememberstack.surfaces.mcp``) tool list."""
    from rememberstack.surfaces.mcp import OperationMcpServer

    extra: dict[str, Any] = {}
    if _accepts(OperationMcpServer.__init__, "processing_layers"):
        extra["processing_layers"] = _UnconfiguredLayers(claims=claims, facts=facts)
    return OperationMcpServer(**ports, **extra).list_tools()["tools"]


def _engine_mcp(deployment: dict[str, Any]) -> dict[str, Any]:
    """``remember-mcp`` engine-mode tool lists over that ``GET /deployment``."""
    from remember.mcp_engine import EngineMcpServer
    from remember.models import DeploymentBuildInfo

    info = DeploymentBuildInfo.model_validate(deployment)
    client = SimpleNamespace(deployment_build_info=lambda: info)
    rendered: dict[str, Any] = {}
    for label, path_ingest, read_only in (
        ("http", False, False),
        ("stdio", True, False),
        ("read_only", False, True),
    ):
        server = EngineMcpServer(
            client=client,  # type: ignore[arg-type]
            path_ingest=path_ingest,
            read_only=read_only,
        )
        rendered[label] = server.list_tools()["tools"]
    return rendered


def _readiness() -> list[list[str]]:
    """The self-host profile's expected per-version stages, in readiness order."""
    from rememberstack.profiles.selfhost import _expected_components

    return [
        [str(stage.value), version] for stage, version in _expected_components().items()
    ]


def _manifest() -> dict[str, str]:
    """The query-surface manifest hash and the digest of the whole manifest."""
    from rememberstack.spine.query_space.canonical import surface_manifest_hash
    from rememberstack.spine.query_space.manifest import build_hash_members
    from rememberstack.spine.query_space.manifest import build_manifest
    from rememberstack.spine.query_space.manifest import MANIFEST_PATH
    from rememberstack.spine.query_space.manifest import render_manifest

    return {
        "surface_manifest_hash": surface_manifest_hash(build_hash_members()),
        "built_manifest_sha256": hashlib.sha256(
            render_manifest(build_manifest()).encode("utf-8")
        ).hexdigest(),
        "checked_in_manifest_sha256": hashlib.sha256(
            MANIFEST_PATH.read_bytes()
        ).hexdigest(),
    }


def capture_defaults() -> dict[str, Any]:
    """Every database-free default surface D143 §8 freezes, as plain JSON."""
    ports = _ports()
    operations, deployment = _http(ports)
    return {
        "operations": operations,
        "deployment": deployment,
        "mcp_in_process_tools": _in_process_mcp(_ports()),
        "mcp_engine_tools": _engine_mcp(deployment),
        "readiness_expected_components": _readiness(),
        "query_surface_manifest": _manifest(),
    }


def capture_layers_off() -> dict[str, Any]:
    """Pin availability and the full source-discovery descriptor with layers off.

    The default fixture already pins every other schema. Keep this fixture
    focused on whether the same tools and corpus instructions remain available.
    """

    def catalogue(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            tool if tool["name"] == "search_documents" else {"name": tool["name"]}
            for tool in tools
        ]

    result = {}
    for name, claims in (("claims_only", True), ("sources_only", False)):
        operations, deployment = _http(_ports(), claims=claims, facts=False)
        result[name] = {
            "operations": [operation["name"] for operation in operations],
            "deployment": deployment,
            "mcp_in_process_tools": catalogue(
                _in_process_mcp(_ports(), claims=claims, facts=False)
            ),
            "mcp_engine_tools": {
                host: catalogue(tools)
                for host, tools in _engine_mcp(deployment).items()
            },
        }
    return result


def render(defaults: dict[str, Any]) -> str:
    """The fixture's on-disk form: sorted keys, two-space indent, newline."""
    return json.dumps(defaults, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layers-off", action="store_true")
    arguments = parser.parse_args()
    print(
        render(capture_layers_off() if arguments.layers_off else capture_defaults()),
        end="",
    )
