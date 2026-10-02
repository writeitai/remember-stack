#!/usr/bin/env python3
"""Record every Python memory facade method for executable TypeScript conformance.

Fixtures contain only synthetic identifiers and keys. --check executes the
current Python source and compares committed observations without editing them.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import inspect
import json
from pathlib import Path
import tempfile
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel
from pydantic import SecretStr
from pydantic import TypeAdapter

from remember import models
from remember.client import AccountApi
from remember.client import Client
from remember.connection import clear_host_cache
from remember.connection import Connection
from remember.connection import resolve_project
from remember.errors import MemoryApiError
from remember.errors import RateLimited
from remember.issuer import clear_metadata_cache
from remember.issuer import fetch_issuer_metadata
from remember.issuer import signed_key_claims
from remember.mcp_tools import map_error
from remember.mcp_tools import McpMemorySettings
from remember.mcp_tools import memory_tools
from remember.mcp_tools import ToolArgumentError
from remember.mcp_tools import validate_arguments
from remember.query_sandbox.errors import SandboxRejection
from remember.query_sandbox.result import QueryResult
from remember.query_sandbox.result import ResultLimits

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "packages/typescript-client/tests/fixtures/python-methods.json"
ID = "10000000-0000-0000-0000-000000000001"
OTHER_ID = "10000000-0000-0000-0000-000000000002"
STAMP = "2026-10-02T12:00:00+00:00"
REQUIRE = {"pipeline": True, "p1": True, "live_graph": True, "p3": False}


def scenarios() -> list[dict[str, Any]]:
    """Cover each method's defaults and supplied optional arguments explicitly."""
    pairs = [
        ("list_operations", {}, {}),
        (
            "run_operation",
            {"name": "facts_context"},
            {
                "name": "facts_context",
                "arguments": {"query": "fixture", "entity_ids": [ID]},
            },
        ),
        (
            "query_sql",
            {"sql": "SELECT 1"},
            {"sql": "SELECT $1", "parameters": [1, "text", None], "max_rows": 0},
        ),
        (
            "open_query",
            {"sql": "SELECT 1"},
            {"sql": "SELECT $1", "parameters": [1], "max_rows": 7},
        ),
        ("explain_sql", {"sql": "SELECT 1"}, {"sql": "SELECT $1", "parameters": [1]}),
        ("explain_query", {"sql": "SELECT 1"}, {"sql": "SELECT $1", "parameters": [1]}),
        (
            "facts_context",
            {"query": "fixture"},
            {
                "query": "fixture",
                "time": {"mode": "current"},
                "hops": 3,
                "predicate": "works_at",
                "entity_ids": [ID],
            },
        ),
        (
            "combined_context",
            {"query": "fixture"},
            {"query": "fixture", "time": {"mode": "current"}},
        ),
        (
            "claims_and_sources_context",
            {"query": "fixture"},
            {"query": "other", "time": {"mode": "history"}},
        ),
        ("resolve_entity", {"name": "fixture"}, {"name": "other"}),
        ("describe_query_space", {}, {"pattern": "memory_*", "include_examples": True}),
        ("search_query_space", {"query": "fixture"}, {"query": "fixture", "k": 25}),
        ("list_saved_queries", {}, {"namespace": "examples", "status": "active"}),
        (
            "describe_saved_query",
            {"namespace": "examples", "name": "safe"},
            {"namespace": "examples", "name": "safe", "version": 2},
        ),
        (
            "run_saved_query",
            {"namespace": "examples", "name": "safe"},
            {
                "namespace": "examples",
                "name": "safe",
                "version": 2,
                "parameters": [1],
                "max_rows": 9,
            },
        ),
        (
            "resolve",
            {"name": "fixture"},
            {"name": "fixture", "context_entity_ids": [ID, OTHER_ID]},
        ),
        (
            "lookup_relations",
            {},
            {
                "subject_entity_id": ID,
                "predicate": "works_at",
                "object_entity_id": OTHER_ID,
                "valid_at": "2026-10-02T12:00:00+02:00",
                "k": 3,
            },
        ),
        ("transcript_relation", {"relation_id": ID}, {"relation_id": OTHER_ID}),
        (
            "lookup_observations",
            {"entity_id": ID},
            {"entity_id": ID, "property_query": "salary", "k": 3},
        ),
        (
            "search_claims",
            {"query": "fixture"},
            {
                "query": "fixture",
                "k": 3,
                "channel": "bm25",
                "documents": {"language": "en", "authors": ["alice"]},
                "time": {"mode": "at", "at": STAMP},
            },
        ),
        (
            "search_chunks",
            {"query": "fixture"},
            {
                "query": "fixture",
                "k": 3,
                "channel": "bm25",
                "documents": {"family": ["email"], "doc_ids": [ID]},
                "time": {"mode": "history"},
            },
        ),
        ("adjacent_chunks", {"chunk_id": ID}, {"chunk_id": ID, "window": 2}),
        ("hydrate_relation", {"relation_id": ID}, {"relation_id": OTHER_ID}),
        (
            "graph_neighborhood",
            {"entity_id": ID},
            {
                "entity_id": ID,
                "hops": 3,
                "predicates": ["works_at"],
                "valid_at": "2026-10-02T12:00:00+02:00",
                "believed_at": "2026-10-02T12:00:00+02:00",
                "limit": 7,
                "continuation": "fixture-cursor",
                "include_paths": True,
            },
        ),
        (
            "graph_path",
            {"from_entity_id": ID, "to_entity_id": OTHER_ID},
            {
                "from_entity_id": ID,
                "to_entity_id": OTHER_ID,
                "max_hops": 3,
                "predicates": ["works_at"],
                "valid_at": "2026-10-02T12:00:00+02:00",
                "believed_at": "2026-10-02T12:00:00+02:00",
            },
        ),
        (
            "graph_citation_path",
            {"from_doc_id": ID, "to_doc_id": OTHER_ID},
            {"from_doc_id": ID, "to_doc_id": OTHER_ID, "max_hops": 3},
        ),
        ("deployment_build_info", {}, {}),
        (
            "pipeline_readiness",
            {"version_ids": [ID], "require": REQUIRE},
            {"version_ids": [ID, OTHER_ID], "require": {**REQUIRE, "p3": True}},
        ),
        (
            "wait_for_readiness",
            {"version_ids": [ID]},
            {
                "version_ids": [ID],
                "timeout": 1.0,
                "poll_interval": 0.01,
                "require_p3": True,
            },
        ),
        (
            "ingest",
            {"content": {"base64": "bm90ZQ=="}, "filename": "fixture.md"},
            {
                "content": {"base64": "bm90ZQ=="},
                "filename": "fixture.md",
                "mime": "text/plain",
                "title": "fixture title",
                "source_kind": "agent",
                "source_ref": "stable/ref",
                "source_modified_at": STAMP,
                "versioning_mode": "living",
                "source_version_ref": "etag-2",
                "source_path": "/upstream/fixture.md",
            },
        ),
        (
            "list_documents",
            {},
            {"limit": 7, "cursor": "fixture-cursor", "status": "failed"},
        ),
        (
            "search_documents",
            {},
            {
                "query": "fixture",
                "filters": {"authors": ["alice"], "modified_from": STAMP},
                "versions": "all",
                "time": {"mode": "at", "at": STAMP},
                "k": 3,
            },
        ),
        (
            "search_documents_request",
            {"request": {}},
            {
                "request": {
                    "filters": {"language": "en", "authors": ["alice"]},
                    "cursor": "next",
                    "versions": "all",
                    "k": 3,
                }
            },
        ),
        (
            "section_history",
            {"doc_id": ID, "section_key": "part/4:per-diem"},
            {
                "doc_id": ID,
                "section_key": "part/4:per-diem",
                "time": {
                    "mode": "overlap",
                    "from": "2026-10-02T12:00:00+02:00",
                    "to": STAMP,
                },
                "k": 3,
                "cursor": "next",
            },
        ),
        (
            "section_history_request",
            {"request": {"doc_id": ID, "section_key": "part/4:per-diem"}},
            {
                "request": {
                    "doc_id": ID,
                    "section_key": "part/4:per-diem",
                    "time": {"mode": "at", "at": STAMP},
                    "k": 3,
                    "cursor": "next",
                }
            },
        ),
        (
            "set_references",
            {"doc_id": ID, "version_id": OTHER_ID, "references": []},
            {
                "doc_id": ID,
                "version_id": OTHER_ID,
                "references": [
                    {
                        "kind": "amends",
                        "target": {"source_kind": "policy", "source_ref": "expenses"},
                        "change_date_known": False,
                        "context": "café",
                    },
                    {
                        "kind": "refers_to",
                        "target": {
                            "source_kind": "policy",
                            "source_ref": "expenses",
                            "version_key": "edition-2",
                            "section_key": "part/4",
                        },
                        "binding": "pinned",
                        "from_section_key": "part/4",
                    },
                ],
            },
        ),
        (
            "reference_generations",
            {"doc_id": ID, "version_id": OTHER_ID},
            {"doc_id": OTHER_ID, "version_id": ID},
        ),
        (
            "document_references",
            {"doc_id": ID},
            {
                "chunk_id": OTHER_ID,
                "direction": "incoming",
                "kinds": ["amends", "refers_to"],
                "time": {"mode": "at", "at": "2026-10-02T12:00:00+02:00"},
                "k": 3,
                "cursor": "next",
            },
        ),
        (
            "document_references_request",
            {"request": {"doc_id": ID}},
            {
                "request": {
                    "doc_id": ID,
                    "section_key": "part/4",
                    "direction": "outgoing",
                    "kinds": ["cites"],
                    "time": {"mode": "overlap", "from": STAMP, "to": STAMP},
                    "k": 3,
                    "cursor": "next",
                }
            },
        ),
        (
            "set_effective_periods",
            {"doc_id": ID, "version_id": OTHER_ID, "periods": []},
            {
                "doc_id": ID,
                "version_id": OTHER_ID,
                "periods": [
                    {
                        "effective_from": "2026-01-01T00:00:00Z",
                        "effective_until": STAMP,
                    },
                    {"effective_from": "2026-11-01T00:00:00Z"},
                ],
            },
        ),
        ("clear_effective_time", {"doc_id": ID}, {"doc_id": OTHER_ID}),
        ("delete_document", {"doc_id": ID}, {"doc_id": OTHER_ID}),
        ("connectors", {}, {}),
        (
            "add_connector",
            {"connector": {"kind": "custom", "name": "fixture"}},
            {
                "connector": {
                    "kind": "custom",
                    "name": "fixture",
                    "configuration": {"directory": "/sources"},
                    "credential_ref": "operator-secret-reference",
                }
            },
        ),
        ("pause_connector", {"connector_id": ID}, {"connector_id": OTHER_ID}),
        ("connector_status", {"connector_id": ID}, {"connector_id": OTHER_ID}),
    ]
    result = [
        {"method": name, "variant": variant, "options": options}
        for name, omitted, supplied in pairs
        for variant, options in [("defaults", omitted), ("supplied", supplied)]
    ]
    query_args = {
        "query_sql": {"sql": "SELECT 1"},
        "explain_sql": {"sql": "SELECT 1"},
        "describe_query_space": {},
        "search_query_space": {"query": "fixture"},
        "list_saved_queries": {},
        "describe_saved_query": {"namespace": "examples", "name": "safe"},
        "run_saved_query": {"namespace": "examples", "name": "safe"},
    }
    result.extend(
        {
            "method": "call_open_query",
            "variant": name,
            "options": {"name": name, "arguments": arguments},
        }
        for name, arguments in query_args.items()
    )
    result.extend(
        [
            {
                "method": name,
                "variant": "time-only",
                "options": {"query": "fixture", "time": {"mode": "at", "at": STAMP}},
            }
            for name in ("search_claims", "search_chunks")
        ]
    )
    result.extend(
        [
            {
                "method": name,
                "variant": "documents-only",
                "options": {"query": "fixture", "documents": {"language": "en"}},
            }
            for name in ("search_claims", "search_chunks")
        ]
    )
    result.extend(
        [
            {
                "method": "section_history",
                "variant": "time-" + scope["mode"] + str(index),
                "options": {"doc_id": ID, "section_key": "part/4", "time": scope},
            }
            for index, scope in enumerate(
                [
                    {"mode": "current"},
                    {"mode": "history"},
                    {"mode": "at", "at": "2026-01-01T00:00:00.000001Z"},
                    {
                        "mode": "overlap",
                        "from": "2026-01-01T00:00:00Z",
                        "to": "2026-01-01T00:00:00+00:00",
                    },
                ]
            )
        ]
    )
    result.append(
        {
            "method": "ingest",
            "variant": "declared-effective-version",
            "options": {
                "content": {"base64": "bm90ZQ=="},
                "filename": "fixture.md",
                "source_kind": "policy",
                "source_ref": "expenses",
                "version_key": "edition-2",
                "effective_from": "2026-01-01T00:00:00+00:00",
                "effective_until": STAMP,
            },
        }
    )
    result.extend(
        [
            {
                "method": "section_history_request",
                "variant": "compact-uuid",
                "options": {
                    "request": {
                        "doc_id": ID.replace("-", "").upper(),
                        "section_key": "part/4",
                    }
                },
            },
            {
                "method": "document_references_request",
                "variant": "compact-uuid",
                "options": {"request": {"doc_id": ID.replace("-", "").upper()}},
            },
        ]
    )
    result.extend(
        [
            {
                "method": "ingest",
                "variant": "file-mime-before-display-name",
                "options": {"source": "$FILE", "filename": "display.png"},
            },
            {
                "method": "ingest_file",
                "variant": "file-mime-before-display-name",
                "options": {"file_path": "$FILE", "filename": "display.png"},
            },
        ]
    )
    result.extend(
        [
            {
                "method": method,
                "variant": "scope-" + scope["mode"],
                "options": (
                    {"time": scope}
                    if method == "search_documents"
                    else {"request": {"time": scope}}
                ),
            }
            for method in ("search_documents", "search_documents_request")
            for scope in (
                {"mode": "current"},
                {"mode": "history"},
                {"mode": "at", "at": STAMP},
                {"mode": "overlap", "from": STAMP, "to": STAMP},
            )
        ]
    )
    inventory = json.loads(
        (ROOT / "plan/designs/typescript_client_parity.json").read_text()
    )
    wanted = set(inventory["classes"]["MemoryClient"]) - {"__init__", "close"}
    observed = {case["method"] for case in result}
    assert wanted <= observed, f"missing Python method scenarios: {wanted - observed}"
    assert observed - wanted == {"ingest_file"}
    for case in result:
        case["typescriptMethod"] = camel(value=case["method"])
        case["typescriptOptions"] = {
            camel(value=key): value for key, value in case["options"].items()
        }
        for seconds, milliseconds in [
            ("timeout", "timeoutMs"),
            ("poll_interval", "pollIntervalMs"),
        ]:
            if seconds in case["options"]:
                del case["typescriptOptions"][camel(value=seconds)]
                case["typescriptOptions"][milliseconds] = (
                    case["options"][seconds] * 1000
                )
    return result


def camel(*, value: str) -> str:
    """Translate only top-level method/options names; nested wire keys stay snake_case."""
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


def responses() -> dict[str, Any]:
    """Build source-validated complete responses, including recursive default values."""
    now = datetime.fromisoformat(STAMP)
    evidence = models.Envelope(
        grain=models.Grain.EVIDENCE,
        temporal_scope=models.CurrentTemporalScope(evaluated_at=now, believed_at=now),
        freshness=models.Freshness(pg_live_ts=now),
    )
    fact = evidence.model_copy(update={"grain": models.Grain.FACT})
    generation = models.ReferenceGeneration(
        generation_id=UUID(ID),
        doc_id=UUID(ID),
        version_id=UUID(OTHER_ID),
        origin="supplied",
        status="pending",
        input_hash="fixture",
        item_count=2,
        created_at=now,
    )
    return {
        "section_history": models.SectionHistoryPage(
            doc_id=UUID(ID),
            section_key="part/4:per-diem",
            periodised=True,
            rows=(
                models.SectionHistoryRow(
                    version_id=UUID(OTHER_ID),
                    version_no=2,
                    status="absent",
                    effective=(models.EffectiveInterval(from_=now),),
                ),
            ),
            evaluated_at=now,
            believed_at=now,
        ).model_dump(mode="json"),
        "references_set": models.ReferencesSet(
            doc_id=UUID(ID),
            version_id=UUID(OTHER_ID),
            outcome="created",
            generation=generation,
        ).model_dump(mode="json"),
        "reference_generations": models.ReferenceGenerations(
            doc_id=UUID(ID), version_id=UUID(OTHER_ID), generations=(generation,)
        ).model_dump(mode="json"),
        "document_references": models.DocumentReferencesPage(
            rows=(), evaluated_at=now, believed_at=now
        ).model_dump(mode="json"),
        "effective_periods": models.EffectivePeriodsSet(
            doc_id=UUID(ID),
            version_id=UUID(OTHER_ID),
            periods=(
                models.DeclaredEffectivePeriod(
                    period_id=UUID(ID),
                    effective_from=now,
                    effective_until=None,
                    declared_at=now,
                ),
            ),
            declared=1,
            retracted=0,
        ).model_dump(mode="json"),
        "effective_cleared": models.EffectiveTimeCleared(
            doc_id=UUID(ID), retracted=1, cleared_at=now
        ).model_dump(mode="json"),
        "envelope": evidence.model_dump(mode="json"),
        "fact": fact.model_dump(mode="json"),
        "bundle": models.ContextBundleV2(
            claims_and_sources=evidence, facts=fact
        ).model_dump(mode="json"),
        "query": QueryResult(
            request_id=UUID(ID),
            deployment_id=UUID(ID),
            surface_manifest_hash="fixture",
            query_hash="fixture",
            limits=ResultLimits(
                row_cap=10,
                byte_cap=1000,
                statement_timeout_ms=100,
                analytical_tier=False,
            ),
            execution_started_at=now,
            elapsed_ms=0,
        ).model_dump(mode="json"),
        "readiness": models.PipelineReadinessReport(
            ready=True, versions=(), capabilities={}
        ).model_dump(mode="json"),
        "ingest": models.IngestedVersion(
            deployment_id=UUID(ID),
            doc_id=UUID(ID),
            version_id=UUID(OTHER_ID),
            content_hash="fixture",
            created=False,
        ).model_dump(mode="json"),
        "document_page": models.DocumentPage(documents=()).model_dump(mode="json"),
        "search_page": models.DocumentSearchPage(documents=(), as_of=now).model_dump(
            mode="json"
        ),
        "deletion": models.DocumentDeletion(
            doc_id=UUID(ID),
            deleted_at=now,
            claims_retired=0,
            relations_closed=0,
            observations_closed=0,
        ).model_dump(mode="json"),
        "deployment": models.DeploymentBuildInfo().model_dump(mode="json"),
        "connector": models.ConnectorDescriptor(
            connector_id=UUID(ID), kind="custom", name="fixture", status="active"
        ).model_dump(mode="json"),
    }


def select_response(*, request: httpx.Request, fixtures: dict[str, Any]) -> Any:
    """Select a valid response by the actual request, not by the method under test."""
    path = request.url.path
    if path == "/operations":
        return []
    if path == "/operations/combined_context":
        return fixtures["bundle"]
    if path == "/operations/facts_context":
        return fixtures["fact"]
    if path.startswith("/query/"):
        if path in {"/query/space/search", "/query/saved"}:
            return []
        if path in {"/query/space", "/query/saved/examples/safe"}:
            return {}
        return fixtures["query"]
    if "/sections/" in path and path.endswith("/history"):
        return fixtures["section_history"]
    if path.endswith("/references") and "/versions/" in path:
        return fixtures[
            "references_set" if request.method == "PUT" else "reference_generations"
        ]
    if path == "/documents/references":
        return fixtures["document_references"]
    if path.endswith("/effective-periods"):
        return fixtures[
            "effective_periods" if request.method == "PUT" else "effective_cleared"
        ]
    mapping = {
        "/readiness": "readiness",
        "/ingest": "ingest",
        "/deployment": "deployment",
        "/documents": "document_page",
        "/documents/search": "search_page",
    }
    if path in mapping:
        return fixtures[mapping[path]]
    if path.startswith("/documents/") and request.method == "DELETE":
        return fixtures["deletion"]
    if path == "/connectors" and request.method == "GET":
        return []
    if path.startswith("/connectors"):
        return fixtures["connector"]
    return fixtures["envelope"]


def python_options(
    *, options: dict[str, Any], file_path: Path, method: str | None = None
) -> dict[str, Any]:
    """Construct the same typed model/datetime/bytes inputs used by Python callers."""
    result = dict(options)
    for key in ("source", "file_path"):
        if result.get(key) == "$FILE":
            result[key] = file_path
    if isinstance(result.get("content"), dict):
        result["content"] = base64.b64decode(result["content"]["base64"])
    for key in (
        "valid_at",
        "believed_at",
        "source_modified_at",
        "effective_from",
        "effective_until",
    ):
        if key in result:
            result[key] = datetime.fromisoformat(result[key])
    for key, model in {
        "documents": models.DocumentSearchFilters,
        "filters": models.DocumentSearchFilters,
        "request": {
            "section_history_request": models.SectionHistoryRequest,
            "document_references_request": models.DocumentReferencesRequest,
        }.get(method or "", models.DocumentSearchRequest),
        "require": models.ReadinessRequirements,
        "connector": models.ConnectorCreate,
    }.items():
        if key in result:
            result[key] = model.model_validate(result[key])
    if result.get("time") is not None and method in {
        "search_claims",
        "search_chunks",
        "search_documents",
        "section_history",
        "document_references",
    }:
        result["time"] = TypeAdapter(models.ReadTime).validate_python(result["time"])
    for key, model in {
        "references": models.ReferenceInput,
        "periods": models.EffectivePeriodInput,
    }.items():
        if key in result:
            result[key] = tuple(model.model_validate(item) for item in result[key])
    for key in ("version_ids", "context_entity_ids"):
        if key in result:
            result[key] = tuple(UUID(value) for value in result[key])
    return result


def json_result(*, value: Any) -> Any:
    """Render Python model, tuple and dictionary responses into canonical JSON."""
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode()}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BaseModel):
        result = value.model_dump(mode="json")
        # Python model attributes exist even for serialization-excluded fields.
        # The JS public result is the full typed object, not Pydantic JSON output.
        for name, field in type(value).model_fields.items():
            if field.exclude:
                result[field.alias or name] = json_result(value=getattr(value, name))
        return result
    if isinstance(value, (tuple, list)):
        return [json_result(value=item) for item in value]
    if isinstance(value, dict):
        return {key: json_result(value=item) for key, item in value.items()}
    return value


def record() -> dict[str, Any]:
    """Execute the current Python facade against a recording transport."""
    fixtures = responses()
    cases = scenarios()
    with tempfile.TemporaryDirectory(prefix="remember-ts-parity-") as directory:
        path = Path(directory) / "fixture.md"
        path.write_bytes(b"note")
        for case in cases:
            wire: list[dict[str, Any]] = []
            replies: list[Any] = []

            def answer(
                request: httpx.Request,
                *,
                wire: list[dict[str, Any]] = wire,
                replies: list[Any] = replies,
            ) -> httpx.Response:
                """Record one request and return an independently source-validated response."""
                content_type = request.headers.get("content-type")
                body = request.content
                wire.append(
                    {
                        "method": request.method,
                        "path": request.url.raw_path.decode("ascii").split("?", 1)[0],
                        "query": list(request.url.params.multi_items()),
                        "contentType": content_type,
                        "body": json.loads(body)
                        if content_type == "application/json"
                        else None,
                        "contentBase64": base64.b64encode(body).decode()
                        if content_type is not None
                        and content_type != "application/json"
                        else None,
                    }
                )
                if request.method == "POST" and request.url.path == "/documents/search":
                    # The same request model used by the real HTTP route must accept
                    # the bytes; matching two clients is insufficient on its own.
                    models.DocumentSearchRequest.model_validate_json(body)
                response = select_response(request=request, fixtures=fixtures)
                replies.append(response)
                return httpx.Response(200, json=response)

            with Client(
                client=httpx.Client(
                    transport=httpx.MockTransport(answer),
                    base_url="http://fixture.test",
                )
            ) as client:
                method = getattr(client, case["method"])
                inspect.signature(method).bind(
                    **python_options(
                        options=case["options"], file_path=path, method=case["method"]
                    )
                )
                result = method(
                    **python_options(
                        options=case["options"], file_path=path, method=case["method"]
                    )
                )
            case["wire"] = wire
            case["responses"] = replies
            case["result"] = json_result(value=result)
    return {
        "responses": fixtures,
        "cases": cases,
        "lifecycleDispositions": {
            "MemoryClient.__init__": "constructor/injection tests",
            "MemoryClient.close": "close/disposal tests",
            "Client.from_env": "constructor precedence tests",
            "Client.account": "issuer/account loopback HTTP tests",
            "AccountApi.__init__": "issuer/account loopback HTTP tests",
            "AccountApi.get": "issuer/account loopback HTTP tests",
            "AccountApi.whoami": "issuer/account loopback HTTP tests",
        },
    }


def failure_fixtures() -> list[dict[str, Any]]:
    """Record malformed bodies and exact public error/code/status diagnostics."""
    cases: list[dict[str, Any]] = []
    malformed = [
        ("list_operations", {}, {}),
        ("query_sql", {"sql": "SELECT 1"}, {"contract": "QueryResult/v1", "rows": []}),
        (
            "search_query_space",
            {"query": "fixture"},
            [
                {
                    "kind": "view",
                    "name": "fixture",
                    "score": 1,
                    "purpose": "fixture",
                    "tags": [],
                },
                {"kind": "view"},
            ],
        ),
        ("list_saved_queries", {}, [{"name": "partial"}]),
        ("graph_path", {"from_entity_id": ID, "to_entity_id": OTHER_ID}, {}),
        (
            "pipeline_readiness",
            {"version_ids": [ID], "require": REQUIRE},
            {"ready": True},
        ),
        (
            "ingest",
            {"content": {"base64": "bm90ZQ=="}, "filename": "fixture.md"},
            {"created": False},
        ),
        ("list_documents", {}, {}),
        ("search_documents", {}, {}),
        ("section_history", {"doc_id": ID, "section_key": "part/4"}, {}),
        (
            "section_history_request",
            {"request": {"doc_id": ID, "section_key": "part/4"}},
            {},
        ),
        (
            "set_references",
            {"doc_id": ID, "version_id": OTHER_ID, "references": []},
            {},
        ),
        ("reference_generations", {"doc_id": ID, "version_id": OTHER_ID}, {}),
        ("document_references", {"doc_id": ID}, {}),
        ("document_references_request", {"request": {"doc_id": ID}}, {}),
        (
            "set_effective_periods",
            {"doc_id": ID, "version_id": OTHER_ID, "periods": []},
            {},
        ),
        ("clear_effective_time", {"doc_id": ID}, {}),
        ("delete_document", {"doc_id": ID}, {}),
        ("connectors", {}, [{}]),
        ("describe_query_space", {}, []),
    ]
    for method, options, body in malformed:
        cases.append(
            {
                "method": method,
                "options": options,
                "status": 200,
                "body": body,
                "variant": "malformed-response",
            }
        )
    for status, detail in [
        (503, {"code": "pg_unavailable", "message": "offline"}),
        (
            503,
            {
                "code": "pg_unavailable",
                "message": "offline",
                "retryable": True,
                "request_id": "fixture-request",
            },
        ),
        (503, {"code": "pg_unavailable", "message": "offline", "retryable": "yes"}),
        (422, {"code": "pg_unavailable", "message": "wrong-status"}),
        (422, {"code": "unknown", "message": "unknown-code"}),
        (422, {"code": "invalid_parameter", "message": ""}),
        (404, "not found"),
    ]:
        cases.append(
            {
                "method": "query_sql",
                "options": {"sql": "SELECT 1"},
                "status": status,
                "body": {"detail": detail},
                "variant": "error-envelope",
            }
        )
    cases.append(
        {
            "method": "list_operations",
            "options": {},
            "status": 429,
            "body": {"detail": {"code": "concurrency_limited", "message": "limited"}},
            "headers": {"retry-after": "7"},
            "variant": "admission",
        }
    )
    for detail in [
        {},
        {"code": "concurrency_limited"},
        {"code": "x", "message": ""},
        {"message": 42},
        {"message": True},
        {"code": "x", "message": []},
    ]:
        cases.append(
            {
                "method": "list_operations",
                "options": {},
                "status": 429,
                "body": {"detail": detail},
                "variant": "admission-message-fallback",
            }
        )
    with tempfile.TemporaryDirectory(prefix="remember-ts-bad-response-") as directory:
        for case in cases:

            def answer(
                request: httpx.Request, *, case: dict[str, Any] = case
            ) -> httpx.Response:
                """Return the selected synthetic malformed response, never perform network I/O."""
                return httpx.Response(
                    case["status"], json=case["body"], headers=case.get("headers", {})
                )

            with Client(
                client=httpx.Client(
                    transport=httpx.MockTransport(answer),
                    base_url="http://fixture.test",
                )
            ) as client:
                try:
                    getattr(client, case["method"])(
                        **python_options(
                            options=case["options"],
                            file_path=Path(directory) / "fixture.md",
                            method=case["method"],
                        )
                    )
                except MemoryApiError as error:
                    case["error"] = {
                        "class": type(error).__name__,
                        "statusCode": error.status_code,
                        "detail": error.detail,
                        "code": error.code,
                        "retryable": error.retryable,
                        "requestId": error.request_id,
                    }
                    if isinstance(error, RateLimited):
                        case["error"]["retryAfter"] = error.retry_after
                else:
                    raise AssertionError(
                        f"malformed fixture unexpectedly succeeded: {case}"
                    )
            case["typescriptMethod"] = camel(value=case["method"])
            case["typescriptOptions"] = {
                camel(value=key): value for key, value in case["options"].items()
            }
    return cases


def input_validation_fixtures() -> list[dict[str, Any]]:
    """Record new API validation refusals and prove no HTTP call was made."""
    pairs: list[tuple[str, dict[str, Any]]] = [
        ("section_history", {"doc_id": ID, "section_key": key})
        for key in ("", "bad%key", "bad!key", "ž", "x" * 201)
    ]
    pairs.extend(
        [
            ("section_history", {"doc_id": ID, "section_key": "key", "k": 0}),
            (
                "section_history",
                {"doc_id": ID, "section_key": "key", "time": {"at": STAMP}},
            ),
            (
                "section_history",
                {
                    "doc_id": ID,
                    "section_key": "key",
                    "time": {"mode": "at", "at": "2026-01-01T00:00:00"},
                },
            ),
            (
                "section_history",
                {
                    "doc_id": ID,
                    "section_key": "key",
                    "time": {
                        "mode": "overlap",
                        "from": "2026-01-01T00:00:00.000002Z",
                        "to": "2026-01-01T00:00:00.000001Z",
                    },
                },
            ),
            ("document_references", {}),
            ("document_references", {"doc_id": ID, "chunk_id": ID}),
            ("document_references", {"chunk_id": ID, "section_key": "key"}),
            ("document_references", {"doc_id": ID, "kinds": []}),
            (
                "document_references_request",
                {"request": {"doc_id": ID, "unknown": True}},
            ),
            (
                "set_references",
                {
                    "doc_id": ID,
                    "version_id": OTHER_ID,
                    "references": [
                        {
                            "kind": "cites",
                            "binding": "pinned",
                            "target": {
                                "source_kind": "policy",
                                "source_ref": "expenses",
                            },
                        }
                    ],
                },
            ),
        ]
    )
    pairs.extend(
        [
            (
                "set_references",
                {
                    "doc_id": ID,
                    "version_id": OTHER_ID,
                    "references": [
                        {
                            "kind": "amends",
                            "target": {
                                "source_kind": "policy",
                                "source_ref": "expenses",
                            },
                            **changes,
                        }
                    ],
                },
            )
            for changes in (
                {},
                {"change_date_known": True},
                {"change_date_known": False, "change_effective_from": STAMP},
                {
                    "change_date_known": True,
                    "change_effective_from": "2026-01-01T00:00:00+02:00",
                },
            )
        ]
    )
    pairs.append(
        (
            "set_references",
            {
                "doc_id": ID,
                "version_id": OTHER_ID,
                "references": [
                    {
                        "kind": "cites",
                        "target": {"source_kind": "policy", "source_ref": "expenses"},
                        "context": "\ud800",
                    }
                ],
            },
        )
    )
    pairs.append(
        (
            "set_references",
            {
                "doc_id": ID,
                "version_id": OTHER_ID,
                "references": [
                    {
                        "kind": "cites",
                        "target": {"source_kind": "policy", "source_ref": "expenses"},
                        "change_date_known": False,
                    }
                ],
            },
        )
    )
    pairs.extend(
        [
            (
                "set_effective_periods",
                {"doc_id": ID, "version_id": OTHER_ID, "periods": periods},
            )
            for periods in (
                [{"effective_from": STAMP, "effective_until": STAMP}],
                [{"effective_from": "2026-01-01T00:00:00+02:00"}],
                [
                    {
                        "effective_from": "2026-01-01T00:00:00.000002Z",
                        "effective_until": "2026-01-01T00:00:00.000001Z",
                    }
                ],
                [
                    {"effective_from": "2026-01-01T00:00:00Z"},
                    {"effective_from": "2026-01-01T00:00:00+00:00"},
                ],
            )
        ]
    )
    pairs.extend(
        [
            (
                "ingest",
                {"content": {"base64": "bm90ZQ=="}, "filename": "note.md", **options},
            )
            for options in (
                {"version_key": "edition"},
                {
                    "source_kind": "policy",
                    "source_ref": "expenses",
                    "effective_until": STAMP,
                },
                {
                    "source_kind": "policy",
                    "source_ref": "expenses",
                    "effective_from": STAMP,
                    "versioning_mode": "living",
                },
                {
                    "source_kind": "policy",
                    "source_ref": "expenses",
                    "effective_from": "2026-01-01T00:00:00+02:00",
                },
                {
                    "source_kind": "policy",
                    "source_ref": "expenses",
                    "effective_from": STAMP,
                    "effective_until": STAMP,
                },
            )
        ]
    )
    cases = []
    with tempfile.TemporaryDirectory(prefix="remember-ts-refusals-") as directory:
        for method, options in pairs:
            calls: list[httpx.Request] = []

            def answer(
                request: httpx.Request, *, calls: list[httpx.Request] = calls
            ) -> httpx.Response:
                """Detect an invalid argument unexpectedly reaching a backend."""
                calls.append(request)
                return httpx.Response(500)

            with Client(
                client=httpx.Client(
                    base_url="http://fixture.test",
                    transport=httpx.MockTransport(answer),
                )
            ) as client:
                try:
                    getattr(client, method)(
                        **python_options(
                            options=options,
                            file_path=Path(directory) / "note.md",
                            method=method,
                        )
                    )
                except (ValueError, TypeError):
                    assert not calls, f"invalid {method} reached HTTP"
                else:
                    raise AssertionError(f"invalid {method} succeeded")
            cases.append(
                {
                    "method": method,
                    "typescriptMethod": camel(value=method),
                    "typescriptOptions": {
                        camel(value=key): value for key, value in options.items()
                    },
                }
            )
    return cases


def tool_fixtures() -> list[dict[str, Any]]:
    """Run every catalogue validator on shared positive and negative arguments."""
    valid = {
        "ingest": {"text": "fixture", "filename": "fixture.md"},
        "pipeline_readiness": {"version_ids": [ID], "require": REQUIRE},
        "delete_document": {"doc_id": ID},
        "search_documents": {"language": "en", "authors": ["alice"]},
        "adjacent_chunks": {"chunk_id": ID},
        "section_history": {
            "doc_id": ID,
            "section_key": "part/4",
            "time": {"mode": "history"},
        },
        "document_references": {"doc_id": ID, "time": {"mode": "at", "at": STAMP}},
        "resolve_entity": {"name": "fixture"},
        "claims_and_sources_context": {"query": "fixture"},
        "facts_context": {"query": "fixture"},
        "combined_context": {"query": "fixture"},
        "query_sql": {"sql": "SELECT 1"},
        "explain_sql": {"sql": "SELECT 1"},
        "describe_query_space": {},
        "search_query_space": {"query": "fixture"},
        "list_saved_queries": {},
        "describe_saved_query": {"namespace": "examples", "name": "safe"},
        "run_saved_query": {"namespace": "examples", "name": "safe"},
    }
    assert set(valid) == {definition.name for definition in memory_tools()}
    cases = []
    for name, arguments in valid.items():
        cases.extend(
            [
                {"name": name, "arguments": arguments, "variant": "valid"},
                {
                    "name": name,
                    "arguments": {**arguments, "unknown": True},
                    "variant": "unknown-key",
                },
            ]
        )
    cases.extend(
        [
            {"name": "ingest", "arguments": arguments, "variant": "invalid-body"}
            for arguments in [
                {"path": "/host/file", "text": "fixture", "filename": "fixture.md"},
                {"text": "fixture", "filename": "fixture.md", "source_kind": "agent"},
                {"content_base64": "AA", "filename": "fixture.bin"},
                {"text": "", "filename": "fixture.md"},
            ]
        ]
    )
    cases.extend(
        [
            {
                "name": "ingest",
                "variant": "effective-period",
                "arguments": {
                    "text": "note",
                    "filename": "note.md",
                    "source_kind": "policy",
                    "source_ref": "expenses",
                    "version_key": "edition-2",
                    "effective_from": STAMP,
                },
            },
            {
                "name": "ingest",
                "variant": "effective-lineage-refusal",
                "arguments": {
                    "text": "note",
                    "filename": "note.md",
                    "version_key": "edition-2",
                },
            },
            {
                "name": "section_history",
                "variant": "invalid-key",
                "arguments": {"doc_id": ID, "section_key": "bad%key"},
            },
            {
                "name": "section_history",
                "variant": "invalid-time",
                "arguments": {
                    "doc_id": ID,
                    "section_key": "key",
                    "time": {
                        "mode": "overlap",
                        "from": STAMP,
                        "to": "2026-01-01T00:00:00Z",
                    },
                },
            },
            {
                "name": "document_references",
                "variant": "two-sources",
                "arguments": {"doc_id": ID, "chunk_id": ID},
            },
        ]
    )
    for case in cases:
        try:
            result = validate_arguments(
                case["name"], case["arguments"], settings=McpMemorySettings()
            )
        except ToolArgumentError as error:
            case["mapped"] = map_error(error).as_dict()
            case["error"] = {
                "class": "ToolArgumentError",
                "code": error.error.code,
                "statusCode": error.error.status_code,
                "retryable": error.error.retryable,
            }
        except SandboxRejection as error:
            case["mapped"] = map_error(error).as_dict()
            case["error"] = {"class": "InputValidationError", "code": error.code.value}
        else:
            case["result"] = json_result(value=result)
    return cases


def error_mapping_fixtures() -> list[dict[str, Any]]:
    """Pin all public HTTP/tool mapping branches to executed Python source."""
    cases = []
    for status, detail, code in [
        (0, "network failed", None),
        (429, "limited", "concurrency_limited"),
        (413, "too large", None),
        (400, "empty_body:fixture", None),
        (409, "spend_cap:fixture", None),
        (409, "dispatch_refused:fixture", None),
        (409, "dispatch_parked:fixture", None),
        (401, "", None),
        (403, "", None),
        (500, "failed", None),
        (404, "missing", None),
        (409, "revalidate", "saved_query_revalidation_pending"),
        (422, "query defect", "invalid_parameter"),
    ]:
        options = {
            "statusCode": status,
            "detail": detail,
            "code": code,
            "requestId": "fixture-request",
        }
        error = (
            MemoryApiError(
                status_code=status,
                detail=detail,
                code=code,
                request_id="fixture-request",
            )
            if status != 429
            else RateLimited(detail=detail, code=code, retry_after=7)
        )
        if status == 429:
            options["retryAfter"] = 7
            options["requestId"] = None
        cases.append(
            {
                "options": options,
                "class": type(error).__name__,
                "result": map_error(error).as_dict(),
            }
        )
    timeout = TimeoutError("timed out waiting for pipeline readiness")
    cases.append(
        {
            "options": {"detail": str(timeout)},
            "class": "TimeoutError",
            "result": map_error(timeout).as_dict(),
        }
    )
    return cases


def account_error_fixtures() -> list[dict[str, Any]]:
    """Execute the issuer account decoder separately from the deployment decoder."""
    issuer = "https://account-fixture.invalid"
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"iss": issuer, "projects": ["fixture"]}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    key = "eyJhbGciOiJFUzI1NiJ9." + payload + ".fixture"
    cases = []
    for path, status, body in [
        (
            "/v1/keys/self",
            403,
            {"detail": {"code": "forbidden", "message": "Key revoked"}},
        ),
        ("/v1/keys/self", 403, {"detail": "x", "extra": 1}),
        (
            "/query/foo",
            400,
            {"detail": {"code": "bad_argument", "message": "Fix argument"}},
        ),
        ("/v1/keys/self", 429, {"detail": {"code": "concurrency_limited"}}),
        ("/v1/keys/self", 429, {"detail": {"code": "x", "message": ""}}),
        ("/v1/keys/self", 429, {"detail": {"message": 42}}),
        ("/v1/keys/self", 403, {"detail": None}),
    ]:

        def answer(
            request: httpx.Request, *, status: int = status, body: dict[str, Any] = body
        ) -> httpx.Response:
            """Serve synthetic issuer discovery and the selected account refusal."""
            if request.url.path.startswith("/.well-known"):
                return httpx.Response(
                    200,
                    json={
                        "issuer": issuer,
                        "remember_account_endpoint": issuer + "/account",
                    },
                )
            return httpx.Response(status, json=body)

        with Client(
            api_key=key,
            base_url="https://engine-fixture.invalid",
            transport=httpx.MockTransport(answer),
        ) as client:
            try:
                client.account.get(path)
            except MemoryApiError as error:
                cases.append(
                    {
                        "issuer": issuer,
                        "key": key,
                        "path": path,
                        "status": status,
                        "body": body,
                        "error": {
                            "class": type(error).__name__,
                            "detail": error.detail,
                            "statusCode": error.status_code,
                            "code": error.code,
                        },
                    }
                )
            else:
                raise AssertionError("account error fixture unexpectedly succeeded")
    return cases


def issuer_redirect_fixtures() -> list[dict[str, Any]]:
    """Record redirects and metadata HTTP failures through all issuer call paths."""
    cases: list[dict[str, Any]] = []
    for stage in ("account", "metadata", "project"):
        for status, location, metadata_failure in [
            (300, None, False),
            (302, None, False),
            (304, None, False),
            (307, None, False),
            (300, "/final", False),
            *[(status, None, True) for status in (201, 204, 401, 404, 500)],
        ]:
            clear_metadata_cache()
            clear_host_cache()
            issuer = "https://redirect-fixture.invalid"
            payload = (
                base64.urlsafe_b64encode(
                    json.dumps({"iss": issuer, "projects": ["fixture"]}).encode()
                )
                .rstrip(b"=")
                .decode()
            )
            key = "eyJhbGciOiJFUzI1NiJ9." + payload + ".fixture"
            claims = signed_key_claims(key)
            assert claims is not None
            metadata = {
                "issuer": issuer,
                "remember_account_endpoint": issuer + "/account",
                "remember_project_endpoint": issuer + "/project",
            }
            project = {
                "project": "fixture",
                "name": "fixture",
                "api_url": "https://engine-fixture.invalid",
            }
            wire: list[dict[str, str]] = []
            replies: list[dict[str, Any]] = []

            def answer(
                request: httpx.Request,
                *,
                stage: str = stage,
                status: int = status,
                location: str | None = location,
                metadata_failure: bool = metadata_failure,
                metadata: dict[str, Any] = metadata,
                project: dict[str, str] = project,
                wire: list[dict[str, str]] = wire,
                replies: list[dict[str, Any]] = replies,
            ) -> httpx.Response:
                """Record actual Python hops and return the selected synthetic redirect."""
                wire.append({"method": request.method, "url": str(request.url)})
                target = (
                    request.url.path.startswith("/.well-known")
                    if metadata_failure
                    else (
                        (
                            stage == "metadata"
                            and request.url.path.startswith("/.well-known")
                        )
                        or (
                            stage == "account"
                            and request.url.path == "/account/v1/keys/self"
                        )
                        or (stage == "project" and request.url.path == "/project")
                    )
                )
                if target:
                    headers = {"location": location} if location else {}
                    replies.append({"status": status, "headers": headers, "body": None})
                    return httpx.Response(status, headers=headers)
                body = (
                    metadata
                    if request.url.path.startswith("/.well-known")
                    or stage == "metadata"
                    else project
                    if stage == "project"
                    else {"ok": True}
                )
                replies.append({"status": 200, "headers": {}, "body": body})
                return httpx.Response(200, json=body)

            case: dict[str, Any] = {
                "stage": stage,
                "status": status,
                "location": location,
                "issuer": issuer,
                "key": key,
                "wire": wire,
                "responses": replies,
            }
            if metadata_failure:
                case["metadataFailure"] = True
            with httpx.Client(transport=httpx.MockTransport(answer)) as http:
                try:
                    if stage == "account":
                        connection = Connection(
                            key=SecretStr(key),
                            key_source="explicit",
                            api_url=None,
                            api_url_source=None,
                            project=None,
                            issuer=issuer,
                            mcp_url=None,
                            stored=None,
                            claims=claims,
                        )
                        result = AccountApi(connection=connection, http=http).get(
                            "/v1/keys/self"
                        )
                    elif stage == "metadata":
                        result = fetch_issuer_metadata(issuer, http=http)
                    else:
                        result = resolve_project(
                            key=key, claims=claims, project=None, http=http
                        )
                except MemoryApiError as error:
                    case["error"] = {
                        "class": type(error).__name__,
                        "statusCode": error.status_code,
                        "code": error.code,
                    }
                else:
                    case["result"] = json_result(value=result)
            cases.append(case)
    clear_metadata_cache()
    clear_host_cache()
    return cases


def main() -> int:
    """Write or check source-executed fixtures; generation never runs in check mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    result = record()
    result["failures"] = failure_fixtures()
    result["inputValidation"] = input_validation_fixtures()
    result["tools"] = tool_fixtures()
    result["errorMappings"] = error_mapping_fixtures()
    result["accountErrors"] = account_error_fixtures()
    result["issuerRedirects"] = issuer_redirect_fixtures()
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.check:
        if not TARGET.exists() or TARGET.read_text() != content:
            raise SystemExit(
                "Python method conformance fixture drift; regenerate and review"
            )
    else:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
