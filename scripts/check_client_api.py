"""Check the tagged engine API against the clients that ship with it."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Iterator
from contextlib import redirect_stderr
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import re
import shlex
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch
from urllib.parse import unquote

from export_openapi import build_document
from export_typescript_parity import ID
from export_typescript_parity import select_response
from export_typescript_parity import TARGET
import httpx
from jsonschema import Draft202012Validator

from remember import cli
from remember.client import Client
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import memory_tools

CONTRACT = Path(__file__).with_name("client_api_contract.json")
# SDK coverage is computed from executable Python/TypeScript recordings.
SDK_EXCLUSIONS = {
    "POST /chunks/adjacent": "SDKs use the equivalent GET route with a bounded window."
}
CLI_COMMANDS = {
    "GET /chunks/{chunk_id}/adjacent": "query adjacent-chunks {id} --window 2",
    "GET /deployment": "doctor",
    "GET /documents": "documents list --limit 7",
    "POST /documents/search": "documents search --limit 7",
    "POST /documents/{doc_id}/archive": "documents archive {id}",
    "POST /documents/{doc_id}/forget": "documents forget {id} --yes",
    "POST /documents/{doc_id}/tags": "documents tag {id} --add fixture",
    "POST /documents/{doc_id}/unarchive": "documents unarchive {id}",
    "GET /forgets": "forget list",
    "GET /forgets/{forget_id}": "forget status {id}",
    "POST /ingest": "ingest {file} --title fixture",
    "GET /operations": "operations list",
    "POST /operations/{name}": "operations run facts_context --arg query=fixture",
    "GET /query/saved": "query list-saved --namespace examples",
    "GET /query/saved/{namespace}/{name}": "query describe-saved examples safe --version 2",
    "POST /query/saved/{namespace}/{name}/run": "query run-saved examples safe --max-rows 7",
    "GET /query/space": "query space --pattern memory_*",
    "GET /query/space/search": "query search-space fixture --k 7",
    "POST /query/sql": "query sql 'SELECT 1' --max-rows 7",
    "POST /query/sql/explain": "query explain-sql 'SELECT 1'",
    "GET /tags": "tags list --query fixture",
    "POST /tags": "tags create fixture --description Fixture",
    "PATCH /tags/{tag_id}": "tags update {id} --description Fixture",
    "DELETE /tags/{tag_id}": "tags delete {id}",
}
# Exclusions are checked against the actual schema and runtime catalogue.
CLI_OMISSIONS = {
    "alternative": {"POST /chunks/adjacent"},
    "sdk_diagnostics": {"GET /processing-layers", "POST /readiness"},
    "sdk_retrieval": {
        "GET /hydrate/relation/{relation_id}",
        "GET /lookup/observations",
        "GET /lookup/relations",
        "GET /resolve",
        "GET /search/chunks",
        "GET /search/claims",
        "GET /transcript/relation/{relation_id}",
        "POST /search/chunks",
        "POST /search/claims",
        "POST /graph/citation-path",
        "POST /graph/neighborhood",
        "POST /graph/path",
    },
    "sdk_history": {
        "DELETE /documents/{doc_id}/effective-periods",
        "GET /documents/{doc_id}/sections/{section_key}/history",
        "GET /documents/{doc_id}/versions/{version_id}/references",
        "POST /documents/references",
        "PUT /documents/{doc_id}/versions/{version_id}/effective-periods",
        "PUT /documents/{doc_id}/versions/{version_id}/references",
    },
}
MCP_OMISSIONS = {
    "alternative": CLI_OMISSIONS["alternative"],
    "sdk_retrieval": CLI_OMISSIONS["sdk_retrieval"],
    "sdk_history": CLI_OMISSIONS["sdk_history"]
    - {
        "GET /documents/{doc_id}/sections/{section_key}/history",
        "POST /documents/references",
    },
    "workspace": {
        "GET /deployment",
        "GET /documents",
        "GET /forgets",
        "GET /forgets/{forget_id}",
        "GET /processing-layers",
    },
    "discovery": {"GET /operations"},
}


def validate_inventory(
    *, surface: str, routes: set[str], covered: set[str], excluded: set[str]
) -> None:
    """Reject missing, obsolete and simultaneously covered and excluded routes."""
    missing = routes - covered - excluded
    stale = (covered | excluded) - routes
    overlap = covered & excluded
    if missing or stale or overlap:
        raise ValueError(
            f"{surface} inventory: missing={sorted(missing)}, "
            f"obsolete={sorted(stale)}, both covered and excluded={sorted(overlap)}"
        )


def route_pattern(*, route: str) -> str:
    """Compile path placeholders as named groups for parameter validation."""
    return re.sub(r"\\\{([^}]+)\\\}", r"(?P<\1>[^/]+)", re.escape(route))


def match_route(*, routes: set[str], method: str, path: str) -> str:
    """Match a recorded wire path to one exported operation template."""
    exact = f"{method} {path}"
    if exact in routes:
        return exact
    matches = [
        route for route in routes if re.fullmatch(route_pattern(route=route), exact)
    ]
    if len(matches) != 1:
        raise ValueError(f"{exact} matches {matches} in client API")
    return matches[0]


def wire_shape(*, document: dict[str, Any], operation: dict[str, Any]) -> str:
    """Fingerprint the wire declaration and every transitively referenced component."""
    fields = ("parameters", "requestBody", "responses", "security")
    wire = {key: operation[key] for key in fields if key in operation}
    dependencies: dict[str, Any] = {}

    def visit(value: Any) -> None:
        """Walk nested objects and follow local references once."""
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if ref not in dependencies:
                    target = document
                    for part in ref.removeprefix("#/").split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                    dependencies[ref] = target
                    visit(target)
            for item in value.values():
                visit(item)

    visit(wire)
    for alternative in wire.get("security", []):
        for name in alternative:
            scheme = document["components"]["securitySchemes"][name]
            dependencies[f"#/components/securitySchemes/{name}"] = scheme
            visit(scheme)
    content = json.dumps([wire, dependencies], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def validate_sample(
    *,
    document: dict[str, Any],
    route: str,
    operation: dict[str, Any],
    wire: dict[str, Any],
    response: Any,
    status: int,
) -> None:
    """Check observed wire parameters, request and response against OpenAPI."""

    def validate(*, schema: dict[str, Any], value: Any) -> None:
        """Resolve schema references in the complete OpenAPI document."""
        validator = Draft202012Validator(
            {**schema, "components": document["components"]},
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        if error := next(validator.iter_errors(value), None):
            raise ValueError(f"{route} {list(error.path)}: {error.message}")

    path = re.fullmatch(route_pattern(route=route), f"{wire['method']} {wire['path']}")
    assert path is not None, (route, wire["path"])
    path_values = {name: [unquote(value)] for name, value in path.groupdict().items()}
    query = httpx.QueryParams(wire["query"])
    declared_query = set()
    for parameter in operation.get("parameters", []):
        name, location = parameter["name"], parameter["in"]
        if location == "query":
            declared_query.add(name)
            values = query.get_list(name)
        elif location == "path":
            values = path_values.get(name, [])
        else:
            values = (
                [wire["headers"][name.lower()]]
                if name.lower() in wire.get("headers", {})
                else []
            )
        if not values:
            # Historical SDK recordings do not store headers, and the current
            # contract declares no required caller-supplied header.
            assert not parameter.get("required", False), (route, name)
            continue
        schema = parameter["schema"]
        value_type = next(
            variant["type"]
            for variant in schema.get("anyOf", [schema])
            if variant.get("type") != "null"
        )
        if value_type == "array":
            value = values
        else:
            assert len(values) == 1, (route, name, values)
            value = values[0]
            if value_type in {"boolean", "integer"}:
                value = json.loads(value)
        validate(schema=schema, value=value)
    assert set(query.keys()) <= declared_query, (
        route,
        set(query.keys()) - declared_query,
    )
    body = operation.get("requestBody", {})
    if wire["contentType"]:
        content = body["content"][wire["contentType"]]
        validate(
            schema=content["schema"],
            value=(
                wire["body"]
                if wire["contentType"] == "application/json"
                else wire["contentBase64"]
            ),
        )
    else:
        assert not body.get("required", False), route
    response_schema = (
        operation["responses"]
        .get(str(status), {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if response_schema:
        validate(schema=response_schema, value=response)
    else:
        # Perimeter denials are verified by test_openapi_export.py. FastAPI
        # does not list the middleware's 401 in these operations.
        assert status == 401, (route, status)


def verify_clients(
    *, document: dict[str, Any], operations: dict[str, Any], fixture: dict[str, Any]
) -> None:
    """Join recorded SDK traffic and executable CLI/MCP cases to the tagged routes."""
    routes = set(operations)
    sdk_routes = set()
    for case in fixture["cases"]:
        for wire, response in zip(case["wire"], case["responses"], strict=True):
            # The optional operator connector methods have their own SDK parity cases.
            if (wire["method"], wire["path"].split("/")[1]) in {
                ("GET", "connectors"),
                ("POST", "connectors"),
            }:
                continue
            route = match_route(routes=routes, method=wire["method"], path=wire["path"])
            sdk_routes.add(route)
            validate_sample(
                document=document,
                route=route,
                operation=operations[route],
                wire=wire,
                response=response,
                status=200,
            )
    validate_inventory(
        surface="Python and TypeScript SDK",
        routes=routes,
        covered=sdk_routes,
        excluded=set(SDK_EXCLUSIONS),
    )
    validate_inventory(
        surface="CLI",
        routes=routes,
        covered=set(CLI_COMMANDS),
        excluded=set().union(*CLI_OMISSIONS.values()),
    )
    tools = {
        definition.name: match_route(routes=routes, method=method, path=path)
        for definition in memory_tools()
        for method, path in [definition.http_route.split(" ", 1)]
    }
    validate_inventory(
        surface="MCP",
        routes=routes,
        covered=set(tools.values()),
        excluded=set().union(*MCP_OMISSIONS.values()),
    )

    responses = fixture["responses"]
    valid_arguments = {
        case["name"]: case["arguments"]
        for case in fixture["tools"]
        if case["variant"] == "valid"
    }
    assert valid_arguments.keys() == tools.keys()
    # Use the ID to target PATCH directly, as the lookup is a separate operation.
    valid_arguments["update_tag"]["tag"] = ID
    for name, route in tools.items():
        for probe in check_mcp(
            name=name, arguments=valid_arguments[name], responses=responses
        ):
            probe.verify(document=document, route=route, operation=operations[route])
    with TemporaryDirectory(prefix="remember-cli-contract-") as directory:
        upload = Path(directory) / "fixture.md"
        upload.write_text("fixture", encoding="utf-8")
        for route, command in CLI_COMMANDS.items():
            argv = [arg.format(id=ID, file=upload) for arg in shlex.split(command)]
            for probe in check_cli(argv=argv, responses=responses):
                probe.verify(
                    document=document, route=route, operation=operations[route]
                )


class WireProbe:
    """Record real HTTP calls while supplying deterministic success and refusal bodies."""

    def __init__(self, *, responses: dict[str, Any], refuses: bool) -> None:
        """Use the shared SDK conformance replies or reject the credential."""
        self.responses = responses
        self.refuses = refuses
        self.calls: list[tuple[dict[str, Any], Any, int]] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Return a fixture or a 401 without contacting a running engine."""
        status = 401 if self.refuses else 200
        response = (
            {"detail": "contract credential refused"}
            if self.refuses
            else select_response(request=request, fixtures=self.responses)
        )
        content_type = request.headers.get("content-type")
        wire = {
            "method": request.method,
            "path": request.url.raw_path.decode().split("?")[0],
            "query": list(request.url.params.multi_items()),
            "headers": request.headers,
            "contentType": content_type,
            "body": json.loads(request.content)
            if content_type == "application/json"
            else None,
            "contentBase64": base64.b64encode(request.content).decode(),
        }
        self.calls.append((wire, response, status))
        return httpx.Response(status, json=response)

    def client(self) -> Client:
        """Construct the real SDK with the probe as its HTTP transport."""
        return Client(
            client=httpx.Client(
                transport=httpx.MockTransport(self.answer),
                base_url="http://fixture.test",
            )
        )

    def verify(
        self, *, document: dict[str, Any], route: str, operation: dict[str, Any]
    ) -> None:
        """Require the command or tool to reach and satisfy its declared operation."""
        matched = [
            (wire, response, status)
            for wire, response, status in self.calls
            if re.fullmatch(
                route_pattern(route=route), f"{wire['method']} {wire['path']}"
            )
        ]
        assert matched, (route, self.calls)
        for wire, response, status in matched:
            validate_sample(
                document=document,
                route=route,
                operation=operation,
                wire=wire,
                response=response,
                status=status,
            )


def check_mcp(
    *, name: str, arguments: dict[str, Any], responses: dict[str, Any]
) -> Iterator[WireProbe]:
    """Invoke every advertised memory tool through its HTTP-backed handler."""
    for refuses in (False, True):
        probe = WireProbe(responses=responses, refuses=refuses)
        with probe.client() as client:
            server = EngineMcpServer(client=client, path_ingest=False)
            result = server.call_tool(name=name, arguments=arguments)
        assert result.get("isError") is refuses, (name, result)
        if refuses:
            assert "contract credential refused" in str(result), (name, result)
        yield probe


def check_cli(*, argv: list[str], responses: dict[str, Any]) -> Iterator[WireProbe]:
    """Exercise the real CLI parser and handler with successful and refused HTTP."""
    for refuses in (False, True):
        probe = WireProbe(responses=responses, refuses=refuses)
        stdout, stderr = StringIO(), StringIO()
        with (
            probe.client() as client,
            patch.object(cli, "_cli_memory_client", return_value=client),
            patch.object(cli, "_settle_journal"),
            patch.object(cli, "MemoryClient", return_value=client),
            patch.object(cli, "_tool_version_mismatches", return_value=[]),
            patch.object(cli, "_check_harness_file", return_value=(True, "")),
            patch("remember.credentials.load_credentials", return_value=None),
            patch("shutil.which", return_value="/test/remember"),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = cli.main(argv)
        assert code == int(refuses), (argv, code, stdout.getvalue(), stderr.getvalue())
        assert stdout.getvalue() or refuses or argv == ["operations", "list"], argv
        if refuses:
            assert (
                "contract credential refused" in stdout.getvalue() + stderr.getvalue()
            ), argv

        yield probe


def main() -> None:
    """Check client coverage before comparing or refreshing the reviewed contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    document = build_document()
    operations = {
        f"{method.upper()} {path}": operation
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
        if isinstance(operation, dict) and "client" in operation.get("tags", [])
    }
    fixture = json.loads(TARGET.read_text(encoding="utf-8"))
    verify_clients(document=document, operations=operations, fixture=fixture)
    new = {
        route: wire_shape(document=document, operation=operation)
        for route, operation in operations.items()
    }
    if args.write:
        CONTRACT.write_text(
            json.dumps(new, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        old = json.loads(CONTRACT.read_text(encoding="utf-8"))
        drift = sorted(
            route
            for route in old.keys() | new.keys()
            if old.get(route) != new.get(route)
        )
        if drift:
            raise SystemExit(
                "Client API wire drift. Reconcile the affected clients before "
                f"running scripts/check_client_api.py --write: {', '.join(drift)}"
            )


if __name__ == "__main__":
    main()
