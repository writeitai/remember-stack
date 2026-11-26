"""The route tools: one MCP tool per public HTTP route, served by `remember mcp`.

`scripts/check_client_api.py` runs every tool once against the OpenAPI
document. These tests pin what that probe cannot see: the schema and the
argument model agree, bad values are refused before any request, the
interesting wire shapes (GET or POST search, NDJSON references), the error
codes an agent acts on, and which sessions list the tools.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import cast

import httpx
import pytest

from remember.client import MemoryClient
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import memory_tools
from remember.mcp_tools import ROUTE_TOOL_NAMES
from remember.mcp_tools import tool
from remember.mcp_tools import ToolArgumentError
from remember.mcp_tools import validate_arguments
from remember.mcp_tools._routes import _MODELS
from rememberstack.surfaces.query_engine import RESOLVE_CONTEXT_LIMIT

_ID = "10000000-0000-0000-0000-000000000001"
_OTHER = "10000000-0000-0000-0000-000000000002"
_STAMP = "2026-10-02T12:00:00Z"
_ENVELOPE = {
    "grain": "fact",
    "temporal_scope": {
        "mode": "current",
        "evaluated_at": _STAMP,
        "believed_at": _STAMP,
        "identity_regime": "current",
    },
    "freshness": {"pg_live_ts": _STAMP},
}
_GENERATION = {
    "generation_id": _ID,
    "doc_id": _ID,
    "version_id": _OTHER,
    "origin": "supplied",
    "status": "pending",
    "input_hash": "h",
    "created_at": _STAMP,
}


def _engine(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    seen: list[httpx.Request],
    served: tuple[str, ...] = ROUTE_TOOL_NAMES,
) -> MemoryClient:
    """A client over a mocked engine that serves ``served`` at their versions."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/deployment":
            tools = {name: tool(name).tool_version for name in served}
            return httpx.Response(200, json={"tools": tools})
        seen.append(request)
        return handler(request)

    return MemoryClient(
        client=httpx.Client(
            base_url="http://engine.test", transport=httpx.MockTransport(respond)
        )
    )


def _text(result: dict[str, object]) -> dict[str, object]:
    content = cast("list[dict[str, str]]", result["content"])
    return json.loads(content[0]["text"])


def _call(
    name: str,
    arguments: dict[str, object],
    *,
    answer: object = _ENVELOPE,
    status: int = 200,
) -> tuple[dict[str, object], list[httpx.Request]]:
    seen: list[httpx.Request] = []
    server = EngineMcpServer(
        client=_engine(lambda _: httpx.Response(status, json=answer), seen=seen),
        path_ingest=False,
    )
    return server.call_tool(name=name, arguments=arguments), seen


@pytest.mark.parametrize("name", ROUTE_TOOL_NAMES)
def test_schema_and_argument_model_name_the_same_arguments(name: str) -> None:
    """The listed schema offers exactly what the parser accepts and requires."""
    schema = tool(name).input_schema
    model = _MODELS[name]
    assert schema["additionalProperties"] is False
    assert set(cast("dict[str, object]", schema["properties"])) == set(
        model.model_fields
    )
    assert set(cast("list[str]", schema.get("required", []))) == {
        field for field, info in model.model_fields.items() if info.is_required()
    }


def test_resolve_context_bound_is_the_engines() -> None:
    assert (
        tool("resolve_entity_in_context").input_schema["properties"][  # type: ignore[index]
            "context_entity_ids"
        ]["maxItems"]
        == RESOLVE_CONTEXT_LIMIT
    )


def test_route_tools_follow_their_routes_permission() -> None:
    """Writes and the forget records need write; every other route tool reads."""
    writes = {
        definition.name
        for definition in memory_tools()
        if definition.name in ROUTE_TOOL_NAMES and definition.mutates
    }
    assert writes == {
        "list_forgets",
        "get_forget",
        "set_references",
        "set_effective_periods",
        "clear_effective_time",
    }
    assert not any(tool(name).destructive for name in ROUTE_TOOL_NAMES)


def test_valid_arguments_are_returned_unchanged() -> None:
    arguments = {"entity_id": _ID, "property_query": "role", "k": 3}
    assert validate_arguments("lookup_observations", arguments) == arguments


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("list_documents", {"limit": 201}, "limit"),
        ("list_documents", {"status": "deleted"}, "status"),
        ("list_documents", {"page": 2}, "Unknown argument keys: page"),
        ("get_forget", {}, "Missing required arguments: forget_id"),
        ("get_forget", {"forget_id": "nope"}, "forget_id"),
        ("search_claims", {"query": "x", "k": 401}, "k"),
        ("search_chunks", {"query": "x", "channel": "fuzzy"}, "channel"),
        ("search_chunks", {"query": "x", "time": {"mode": "never"}}, "time"),
        ("search_claims", {"query": "x", "documents": {"titles": []}}, "documents"),
        (
            "resolve_entity_in_context",
            {"name": "Alex", "context_entity_ids": []},
            "context_entity_ids",
        ),
        (
            "resolve_entity_in_context",
            {"name": "Alex", "context_entity_ids": [_ID] * 9},
            "context_entity_ids",
        ),
        ("lookup_relations", {"valid_at": "2026-10-02T12:00:00"}, "valid_at"),
        ("lookup_relations", {"valid_at": "2026-10-02T12:00:00+02:00"}, "UTC"),
        (
            "lookup_observations",
            {"entity_id": _ID, "property_query": "role", "cursor": "c"},
            "drop one",
        ),
        ("graph_neighborhood", {"entity_id": _ID, "hops": 5}, "hops"),
        (
            "graph_neighborhood",
            {"entity_id": _ID, "valid_at": _STAMP},
            "supplied together",
        ),
        (
            "graph_path",
            {"from_entity_id": _ID, "to_entity_id": _ID, "max_hops": 7},
            "max_hops",
        ),
        (
            "set_references",
            {
                "doc_id": _ID,
                "version_id": _ID,
                "references": [
                    {
                        "kind": "cites",
                        "binding": "pinned",
                        "target": {"source_kind": "k", "source_ref": "r"},
                    }
                ],
            },
            "version_key",
        ),
        (
            "set_references",
            {
                "doc_id": _ID,
                "version_id": _ID,
                "references": [
                    {"kind": "cites", "target": {"source_kind": "k", "source_ref": "r"}}
                ]
                * 1001,
            },
            "references",
        ),
        (
            "set_effective_periods",
            {
                "doc_id": _ID,
                "version_id": _ID,
                "periods": [{"effective_from": _STAMP}, {"effective_from": _STAMP}],
            },
            "same instant",
        ),
        ("clear_effective_time", {"doc_id": "x"}, "doc_id"),
    ],
)
def test_bad_values_are_refused_before_any_request(
    name: str, arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ToolArgumentError, match=message):
        validate_arguments(name, arguments)
    result, seen = _call(name, arguments)
    assert result["isError"] is True
    assert _text(result)["error"]["code"] == "invalid_arguments"  # type: ignore[index]
    assert seen == []


@pytest.mark.parametrize(
    ("arguments", "method"),
    [
        ({"query": "launch"}, "GET"),
        ({"query": "launch", "time": {"mode": "history"}}, "POST"),
        ({"query": "launch", "documents": {"tags": {"any": ["a"]}}}, "POST"),
    ],
)
@pytest.mark.parametrize("name", ["search_claims", "search_chunks"])
def test_a_search_uses_post_whenever_a_filter_travels(
    name: str, arguments: dict[str, object], method: str
) -> None:
    """Without documents or time the SDK sends the same search as GET."""
    result, seen = _call(name, arguments)
    assert result["isError"] is False
    (request,) = seen
    assert request.method == method
    assert request.url.path == "/search/" + name.removeprefix("search_")
    if method == "POST":
        assert json.loads(request.content)["query"] == "launch"
    else:
        assert request.url.params["query"] == "launch"


def test_resolve_entity_in_context_sends_every_context_entity() -> None:
    result, seen = _call(
        "resolve_entity_in_context",
        {"name": "Alex", "context_entity_ids": [_ID, _OTHER]},
    )
    assert result["isError"] is False
    (request,) = seen
    assert request.url.path == "/resolve"
    assert request.url.params.get_list("context_entity_ids") == [_ID, _OTHER]


def test_set_references_sends_the_complete_set_as_ndjson() -> None:
    answer = {
        "doc_id": _ID,
        "version_id": _OTHER,
        "outcome": "created",
        "generation": _GENERATION,
    }
    references = [
        {"kind": "cites", "target": {"source_kind": "policy", "source_ref": "a"}},
        {
            "kind": "amends",
            "from_section_key": "part/4",
            "target": {"source_kind": "policy", "source_ref": "b"},
            "change_date_known": False,
        },
    ]
    result, seen = _call(
        "set_references",
        {"doc_id": _ID, "version_id": _OTHER, "references": references},
        answer=answer,
    )
    assert result["isError"] is False
    assert _text(result)["outcome"] == "created"
    (request,) = seen
    assert request.method == "PUT"
    assert request.url.path == f"/documents/{_ID}/versions/{_OTHER}/references"
    assert request.headers["content-type"] == "application/x-ndjson"
    lines = request.content.decode().splitlines()
    assert [json.loads(line)["target"]["source_ref"] for line in lines] == ["a", "b"]


def test_an_empty_reference_set_is_sent_as_an_empty_body() -> None:
    answer = {
        "doc_id": _ID,
        "version_id": _OTHER,
        "outcome": "created",
        "generation": _GENERATION,
    }
    result, seen = _call(
        "set_references",
        {"doc_id": _ID, "version_id": _OTHER, "references": []},
        answer=answer,
    )
    assert result["isError"] is False
    assert seen[0].content == b""


def test_set_effective_periods_sends_the_periods() -> None:
    answer = {
        "doc_id": _ID,
        "version_id": _OTHER,
        "periods": [],
        "declared": 1,
        "retracted": 0,
    }
    result, seen = _call(
        "set_effective_periods",
        {"doc_id": _ID, "version_id": _OTHER, "periods": [{"effective_from": _STAMP}]},
        answer=answer,
    )
    assert result["isError"] is False
    (request,) = seen
    assert request.method == "PUT"
    assert request.url.path == f"/documents/{_ID}/versions/{_OTHER}/effective-periods"
    assert json.loads(request.content) == {
        "periods": [{"effective_from": "2026-10-02T12:00:00Z", "effective_until": None}]
    }


def test_graph_neighborhood_sends_both_clocks() -> None:
    result, seen = _call(
        "graph_neighborhood",
        {"entity_id": _ID, "valid_at": _STAMP, "believed_at": _STAMP, "hops": 1},
    )
    assert result["isError"] is False
    body = json.loads(seen[0].content)
    assert body["hops"] == 1
    assert body["valid_at"] == body["believed_at"] == "2026-10-02T12:00:00+00:00"


@pytest.mark.parametrize(
    ("name", "arguments", "detail"),
    [
        ("clear_effective_time", {"doc_id": _ID}, "document_not_found"),
        (
            "reference_generations",
            {"doc_id": _ID, "version_id": _ID},
            "version_not_found",
        ),
        ("get_forget", {"forget_id": _ID}, "forget_not_found"),
    ],
)
def test_an_unknown_id_keeps_its_code_and_says_not_to_retry(
    name: str, arguments: dict[str, object], detail: str
) -> None:
    result, _ = _call(name, arguments, answer={"detail": detail}, status=404)
    error = _text(result)["error"]
    assert error == {  # type: ignore[comparison-overlap]
        "code": detail,
        "status_code": 404,
        "detail": f"{detail}: the id is unknown, deleted or forgotten.",
        "retryable": False,
        "agent_action": "Do not retry. Check the id.",
    }


def test_a_disabled_layer_is_layer_unavailable() -> None:
    answer = {
        "detail": {
            "code": "layer_unavailable",
            "layer": "facts",
            "status": "disabled",
            "message": "this deployment does not process the facts layer",
        }
    }
    result, _ = _call(
        "lookup_relations", {"predicate": "works_at"}, answer=answer, status=409
    )
    error = _text(result)["error"]
    assert error["code"] == "layer_unavailable"  # type: ignore[index]
    assert error["status_code"] == 409  # type: ignore[index]


def test_a_conflict_is_reported_with_the_engines_detail() -> None:
    answer = {
        "detail": {
            "code": "effective_period_conflict",
            "message": "another version starts then",
            "version_id": _OTHER,
            "effective_from": _STAMP,
        }
    }
    result, _ = _call(
        "set_effective_periods",
        {"doc_id": _ID, "version_id": _ID, "periods": [{"effective_from": _STAMP}]},
        answer=answer,
        status=409,
    )
    error = _text(result)["error"]
    assert error["status_code"] == 409  # type: ignore[index]
    assert error["retryable"] is False  # type: ignore[index]
    assert "effective_period_conflict" in error["detail"]  # type: ignore[index, operator]


def test_read_only_omits_and_refuses_the_route_writes() -> None:
    seen: list[httpx.Request] = []
    server = EngineMcpServer(
        client=_engine(lambda _: httpx.Response(200, json={}), seen=seen),
        read_only=True,
        path_ingest=False,
    )
    listed = {entry["name"] for entry in server.list_tools()["tools"]}  # type: ignore[union-attr]
    assert "list_documents" in listed
    assert "set_references" not in listed
    assert "list_forgets" not in listed
    result = server.call_tool(name="clear_effective_time", arguments={"doc_id": _ID})
    assert _text(result)["error"]["code"] == "read_only"  # type: ignore[index]
    assert seen == []


@pytest.mark.parametrize(
    ("layers", "hidden"),
    [
        (
            ("sources", "claims"),
            {
                "resolve_entity_in_context",
                "lookup_relations",
                "lookup_observations",
                "hydrate_relation",
                "transcript_relation",
                "graph_neighborhood",
                "graph_path",
            },
        ),
        (
            ("sources",),
            {
                "search_claims",
                "resolve_entity_in_context",
                "lookup_relations",
                "lookup_observations",
                "hydrate_relation",
                "transcript_relation",
                "graph_neighborhood",
                "graph_path",
            },
        ),
        (
            ("facts",),
            {
                "list_documents",
                "search_claims",
                "search_chunks",
                "graph_citation_path",
                "reference_generations",
            },
        ),
    ],
)
def test_a_layer_narrowed_session_lists_the_tools_its_layers_answer(
    layers: tuple[str, ...], hidden: set[str]
) -> None:
    """D143: a session narrowed to some layers hides tools that read only others."""
    seen: list[httpx.Request] = []
    server = EngineMcpServer(
        client=_engine(lambda _: httpx.Response(200, json={}), seen=seen),
        path_ingest=False,
        layers=layers,
    )
    listed = {entry["name"] for entry in server.list_tools()["tools"]}  # type: ignore[union-attr]
    assert listed == set(ROUTE_TOOL_NAMES) - hidden


def test_a_route_tool_is_listed_only_when_the_deployment_serves_it() -> None:
    seen: list[httpx.Request] = []
    server = EngineMcpServer(
        client=_engine(
            lambda _: httpx.Response(200, json={}),
            seen=seen,
            served=("list_documents",),
        ),
        path_ingest=False,
    )
    listed = [entry["name"] for entry in server.list_tools()["tools"]]  # type: ignore[union-attr]
    assert listed == ["list_documents"]
