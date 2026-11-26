"""Keep the published SDK and CLI surfaces complete and the MCP catalogue bounded."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from remember.client import MemoryClient
from remember.mcp_engine import dispatch
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import memory_tools
from remember.mcp_tools import render_tools_list
from remember.models import DeploymentBuildInfo

_ROOT = Path(__file__).resolve().parents[3]
# json.dumps on the full descriptor list uses the same encoding as the MCP servers.
# Baselines: 44,247 HTTP, 44,901 stdio, 48,372 with project routing.
_MCP_CHARACTER_CAP = 50_000
_MCP_ALLOW_LIST = {
    "ingest": "Save a document supplied in chat.",
    "pipeline_readiness": "Check whether a document just ingested is queryable.",
    "archive_document": "Retire a document's testimony at the user's request.",
    "unarchive_document": "Restore a document archived in error.",
    "forget_document": "Honor a confirmed request to erase a document.",
    "search_documents": "Find or browse documents by metadata and content.",
    "section_history": "Follow changes to a cited section over time.",
    "document_references": "Follow citations into and out of a document or passage.",
    "list_tags": "Find the tags available to classify or filter documents.",
    "create_tag": "Register a classification the user wants to apply.",
    "update_tag": "Correct a classification without retagging the documents.",
    "delete_tag": "Remove a classification that the user retired.",
    "update_document_tags": "Classify a document from chat.",
    "resolve_entity": "Disambiguate a named person, place or other entity.",
    "claims_and_sources_context": "Find original passages and claims to cite.",
    "facts_context": "Answer with facts and their supporting evidence.",
    "combined_context": "Compare facts with what sources say in one read.",
    "adjacent_chunks": "Gather the context immediately around a source passage.",
    "query_sql": "Answer questions that need a precise SQL read.",
    "explain_sql": "Inspect a SQL plan before running the query.",
    "describe_query_space": "Discover the views and functions SQL can read.",
    "search_query_space": "Find relevant views or functions by description.",
    "list_saved_queries": "Discover reusable queries that already exist.",
    "describe_saved_query": "Check a saved query's inputs and result columns.",
    "run_saved_query": "Reuse a saved query with the required inputs.",
}


def test_public_surface_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate real CLI requests, SDK parity and the full memory tool lists."""
    monkeypatch.syspath_prepend(str(_ROOT / "scripts"))
    from check_client_api import PARITY
    from check_client_api import public_operations
    from check_client_api import TARGET
    from check_client_api import verify_clients

    definitions = memory_tools()
    client = Mock(spec=MemoryClient)
    client.deployment_build_info.return_value = DeploymentBuildInfo(
        build_revision="fixture",
        tools={definition.name: definition.tool_version for definition in definitions},
    )
    catalogues = {
        "project-scoped": render_tools_list(
            definitions, project=True, path_ingest=False, read_only=False
        )
    }
    for transport, path_ingest in (("HTTP", False), ("stdio", True)):
        reply = dispatch(
            server=EngineMcpServer(client=client, path_ingest=path_ingest),
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert reply is not None, transport
        result = reply.get("result")
        assert isinstance(result, dict) and isinstance(result.get("tools"), list), reply
        catalogues[transport] = result["tools"]

    for name, descriptors in catalogues.items():
        listed = {str(descriptor["name"]) for descriptor in descriptors}
        allowed = _MCP_ALLOW_LIST.keys()
        assert listed == allowed and len(descriptors) == len(allowed), (
            f"{name} MCP tools/list has {len(descriptors)} entries; "
            f"unlisted={sorted(listed - allowed)}, missing={sorted(allowed - listed)}"
        )
        characters = len(json.dumps(descriptors))
        assert characters < _MCP_CHARACTER_CAP, (
            f"{name} MCP tools/list descriptors serialize to {characters:,} characters; "
            f"keep them under the {_MCP_CHARACTER_CAP:,}-character cap."
        )
        # The bridge uses readOnlyHint to enforce read-only mode.
        for descriptor in descriptors:
            assert set(descriptor) == {
                "name",
                "description",
                "inputSchema",
                "annotations",
            }

    document = json.loads((_ROOT / "openapi.json").read_text(encoding="utf-8"))
    verify_clients(
        document=document,
        operations=public_operations(document),
        fixture=json.loads(TARGET.read_text(encoding="utf-8")),
        parity=json.loads(PARITY.read_text(encoding="utf-8")),
    )
