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
from export_typescript_parity import camel
from export_typescript_parity import ID
from export_typescript_parity import OTHER_ID
from export_typescript_parity import select_response
from export_typescript_parity import TARGET
import httpx
from jsonschema import Draft202012Validator

from remember import cli
from remember.client import Client
from remember.mcp_engine import EngineMcpServer
from remember.mcp_tools import memory_tools

CONTRACT = Path(__file__).with_name("client_api_contract.json")
PARITY = TARGET.parents[2] / "contracts/parity.json"
# The same exclusion applies to both SDKs and the CLI.
SURFACE_EXCLUSIONS = {
    "POST /chunks/adjacent": "The GET form accepts the same chunk ID and window and returns the same envelope."
}
CLI_COMMANDS = {
    "GET /chunks/{chunk_id}/adjacent": "query adjacent-chunks {id} --window 2",
    "GET /deployment": "deployment",
    "GET /documents": "documents list --limit 7",
    "POST /documents/references": "documents references --doc-id {id} --kind cites",
    "POST /documents/search": "documents search --limit 7",
    "POST /documents/{doc_id}/archive": "documents archive {id}",
    "DELETE /documents/{doc_id}/effective-periods": "documents clear-effective-periods {id}",
    "POST /documents/{doc_id}/forget": "documents forget {id} --yes",
    "GET /documents/{doc_id}/sections/{section_key}/history": (
        "documents section-history {id} part/4:approvals --limit 7"
    ),
    "POST /documents/{doc_id}/tags": "documents tag {id} --add fixture",
    "POST /documents/{doc_id}/unarchive": "documents unarchive {id}",
    "PUT /documents/{doc_id}/versions/{version_id}/effective-periods": (
        "documents set-effective-periods {id} {other_id} --periods "
        """'[{{"effective_from":"2026-10-02T12:00:00Z"}}]'"""
    ),
    "GET /documents/{doc_id}/versions/{version_id}/references": (
        "documents reference-generations {id} {other_id}"
    ),
    "PUT /documents/{doc_id}/versions/{version_id}/references": (
        "documents set-references {id} {other_id} {references}"
    ),
    "GET /forgets": "forget list",
    "GET /forgets/{forget_id}": "forget status {id}",
    "POST /graph/citation-path": "graph citation-path {id} {other_id} --max-hops 2",
    "POST /graph/neighborhood": "graph neighborhood {id} --limit 40 --include-paths",
    "POST /graph/path": "graph path {id} {other_id} --predicate leads",
    "GET /hydrate/relation/{relation_id}": "relation hydrate {id}",
    "POST /ingest": "ingest {file} --title fixture",
    "GET /lookup/observations": "lookup observations {id} --property-query team --k 7",
    "GET /lookup/relations": "lookup relations --subject-entity-id {id} --limit 7",
    "GET /operations": "operations list",
    "POST /operations/{name}": "operations run facts_context --arg query=fixture",
    "GET /processing-layers": "processing-layers",
    "GET /query/saved": "query list-saved --namespace examples",
    "GET /query/saved/{namespace}/{name}": "query describe-saved examples safe --version 2",
    "POST /query/saved/{namespace}/{name}/run": "query run-saved examples safe --max-rows 7",
    "GET /query/space": "query space --pattern memory_*",
    "GET /query/space/search": "query search-space fixture --k 7",
    "POST /query/sql": "query sql 'SELECT 1' --max-rows 7",
    "POST /query/sql/explain": "query explain-sql 'SELECT 1'",
    "POST /readiness": "readiness {id} {other_id} --require-p3",
    "GET /resolve": "resolve Dana --context-entity-id {id}",
    "GET /search/chunks": "search chunks fixture --channel bm25",
    "POST /search/chunks": """search chunks fixture --documents '{{"family":["text"]}}'""",
    "GET /search/claims": "search claims fixture --k 7",
    "POST /search/claims": """search claims fixture --time '{{"mode":"history"}}'""",
    "GET /tags": "tags list --query fixture",
    "POST /tags": "tags create fixture --description Fixture",
    "PATCH /tags/{tag_id}": "tags update {id} --description Fixture",
    "DELETE /tags/{tag_id}": "tags delete {id}",
    "GET /transcript/relation/{relation_id}": "relation transcript {id}",
}
MCP_OMISSIONS = {
    "The GET form covers the same bounded window.": set(SURFACE_EXCLUSIONS),
    "Shell-less clients use assured context and the query space for retrieval.": {
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
    "Version-specific writes and generation tracking belong in CLI or SDK workflows.": {
        "DELETE /documents/{doc_id}/effective-periods",
        "GET /documents/{doc_id}/versions/{version_id}/references",
        "PUT /documents/{doc_id}/versions/{version_id}/effective-periods",
        "PUT /documents/{doc_id}/versions/{version_id}/references",
    },
    "Deployment diagnostics and forget audits are operator workflows.": {
        "GET /deployment",
        "GET /forgets",
        "GET /forgets/{forget_id}",
        "GET /processing-layers",
    },
    "The document search tool also browses documents.": {"GET /documents"},
    "MCP discovery uses tools/list.": {"GET /operations"},
}


def public_operations(document: dict[str, Any]) -> dict[str, Any]:
    """Read HTTP operations from the exported public paths."""
    return {
        f"{method.upper()} {path}": operation
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
        if method
        in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
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
    *,
    document: dict[str, Any],
    operations: dict[str, Any],
    fixture: dict[str, Any],
    parity: dict[str, Any],
) -> None:
    """Join recorded SDK traffic and executable CLI/MCP cases to the tagged routes."""
    routes = set(operations)
    sdk_methods = parity["classes"]["MemoryClient"] | parity["classes"]["Client"]
    sdk_routes = set()
    for case in fixture["cases"]:
        method = case["method"]
        assert callable(getattr(Client, method, None)), (
            f"Missing Python method {method}"
        )
        assert method in sdk_methods and case["typescriptMethod"] == camel(
            value=method
        ), f"Missing or mismatched TypeScript parity method for {method}"
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
        excluded=set(SURFACE_EXCLUSIONS),
    )
    validate_inventory(
        surface="CLI",
        routes=routes,
        covered=set(CLI_COMMANDS),
        excluded=set(SURFACE_EXCLUSIONS),
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
        references = Path(directory) / "references.ndjson"
        references.write_text(
            json.dumps(
                {
                    "kind": "cites",
                    "target": {"source_kind": "file", "source_ref": "fixture.md"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        for route, command in CLI_COMMANDS.items():
            argv = [
                arg.format(id=ID, other_id=OTHER_ID, file=upload, references=references)
                for arg in shlex.split(command)
            ]
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
            patch("remember.credentials.load_credentials", return_value=None),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = cli.main([*argv, "--json"])
        assert code == int(refuses), (argv, code, stdout.getvalue(), stderr.getvalue())
        if refuses:
            assert not stdout.getvalue(), argv
            assert (
                "contract credential refused"
                in json.loads(stderr.getvalue())["error"]["detail"]
            ), argv
        else:
            json.loads(stdout.getvalue())

        yield probe


def main() -> None:
    """Check client coverage before comparing or refreshing the reviewed contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    document = build_document()
    operations = public_operations(document)
    fixture = json.loads(TARGET.read_text(encoding="utf-8"))
    parity = json.loads(PARITY.read_text(encoding="utf-8"))
    verify_clients(
        document=document, operations=operations, fixture=fixture, parity=parity
    )
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
