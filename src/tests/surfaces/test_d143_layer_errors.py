"""D143 §7.2: layer refusals keep one code on every surface.

A disabled layer is ``layer_unavailable`` (409) and a selection the read
cannot honour is ``invalid_parameter`` (422), whether the caller uses the
HTTP API through the Python SDK, ``remember mcp`` in engine mode, or the
in-process MCP server.
"""

from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest

from remember.client import MemoryClient
from remember.errors import LayerUnavailable
from remember.errors import MemoryApiError
from remember.mcp_engine import EngineMcpServer
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.processing_layers import LayerSelectionError
from rememberstack.model.processing_layers import LayerUnavailableError
from rememberstack.surfaces.mcp import OperationMcpServer

_DEPLOYMENT_ID = UUID("d1430000-0000-0000-0000-0000000000e1")
_SELECTION = "this request needs the facts layer, which the layers selection excludes"
_DISABLED = "this deployment does not process the facts layer"

_RESPONSES = {
    "selection": httpx.Response(
        422, json={"detail": {"code": "invalid_parameter", "message": _SELECTION}}
    ),
    "disabled": httpx.Response(
        409,
        json={
            "detail": {
                "code": "layer_unavailable",
                "layer": "facts",
                "status": "disabled",
                "message": _DISABLED,
            }
        },
    ),
}
_RAISED = {
    "selection": LayerSelectionError(layer=ProcessingLayer.FACTS, message=_SELECTION),
    "disabled": LayerUnavailableError(layer=ProcessingLayer.FACTS, message=_DISABLED),
}


def _client(case: str) -> MemoryClient:
    response = _RESPONSES[case]
    return MemoryClient(
        client=httpx.Client(
            base_url="http://engine.test",
            transport=httpx.MockTransport(lambda _request: response),
        )
    )


class _RefusingSurface:
    """An operation surface whose every run raises the layer refusal."""

    deployment_id = _DEPLOYMENT_ID

    def __init__(self, *, error: Exception) -> None:
        self._error = error

    def descriptors(self) -> tuple[object, ...]:
        return ()

    def run(self, **_: object) -> object:
        raise self._error


def _tool_error(result: dict[str, object]) -> dict[str, object]:
    assert result["isError"] is True
    content = result["content"]
    assert isinstance(content, list)
    return json.loads(content[0]["text"])["error"]


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [("selection", 422, "invalid_parameter"), ("disabled", 409, "layer_unavailable")],
)
def test_sdk_and_both_mcp_hosts_report_the_same_code(
    case: str, status: int, code: str
) -> None:
    """SDK error, engine-mode MCP and in-process MCP agree on code and status."""
    with pytest.raises(MemoryApiError) as raised:
        _client(case).run_operation(
            name="facts_context", arguments={"query": "tariff"}, layers=["sources"]
        )
    assert (raised.value.status_code, raised.value.code) == (status, code)
    assert isinstance(raised.value, LayerUnavailable) is (case == "disabled")
    assert raised.value.detail == (_SELECTION if case == "selection" else _DISABLED)

    engine = _tool_error(
        EngineMcpServer(client=_client(case), path_ingest=False).call_tool(
            name="facts_context", arguments={"query": "tariff"}
        )
    )
    local = _tool_error(
        OperationMcpServer(
            surface=_RefusingSurface(error=_RAISED[case])  # type: ignore[arg-type]
        ).call_tool(name="facts_context", arguments={"query": "tariff"})
    )
    for error in (engine, local):
        assert (error["code"], error["status_code"]) == (code, status)
    assert engine["agent_action"] == local["agent_action"]
