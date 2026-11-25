"""Check the tagged engine API against the clients that ship with it."""

from __future__ import annotations

import argparse
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

from export_openapi import build_document
from export_typescript_parity import ID
from export_typescript_parity import select_response
from export_typescript_parity import TARGET
import httpx

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


def match_route(*, routes: set[str], method: str, path: str) -> str:
    """Match a recorded wire path to one exported operation template."""
    exact = f"{method} {path}"
    if exact in routes:
        return exact
    matches = [
        route
        for route in routes
        if re.fullmatch(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(route)), exact)
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


def verify_clients(*, routes: set[str], fixture: dict[str, Any]) -> None:
    """Join recorded SDK traffic and executable CLI/MCP cases to the tagged routes."""
    sdk_routes = {
        match_route(routes=routes, method=wire["method"], path=wire["path"])
        for case in fixture["cases"]
        for wire in case["wire"]
        # The SDK also ships optional operator connector methods. Their
        # conformance runs in check:parity outside the public engine inventory.
        if (wire["method"], wire["path"].split("/")[1])
        not in {("GET", "connectors"), ("POST", "connectors")}
    }
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
        check_mcp(
            name=name, route=route, arguments=valid_arguments[name], responses=responses
        )
    with TemporaryDirectory(prefix="remember-cli-contract-") as directory:
        upload = Path(directory) / "fixture.md"
        upload.write_text("fixture", encoding="utf-8")
        for route, command in CLI_COMMANDS.items():
            argv = [arg.format(id=ID, file=upload) for arg in shlex.split(command)]
            check_cli(argv=argv, route=route, responses=responses)


class WireProbe:
    """Record real HTTP calls while supplying deterministic success and refusal bodies."""

    def __init__(self, *, responses: dict[str, Any], refuses: bool) -> None:
        """Use the shared SDK conformance replies or reject the credential."""
        self.responses = responses
        self.refuses = refuses
        self.calls: list[str] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Return a fixture or a 401 without contacting a running engine."""
        self.calls.append(
            f"{request.method} {request.url.raw_path.decode().split('?')[0]}"
        )
        if self.refuses:
            return httpx.Response(401, json={"detail": "contract credential refused"})
        return httpx.Response(
            200, json=select_response(request=request, fixtures=self.responses)
        )

    def client(self) -> Client:
        """Construct the real SDK with the probe as its HTTP transport."""
        return Client(
            client=httpx.Client(
                transport=httpx.MockTransport(self.answer),
                base_url="http://fixture.test",
            )
        )

    def assert_route(self, *, route: str) -> None:
        """An invocation must reach the declared operation, even on refusal."""
        assert any(
            re.fullmatch(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(route)), call)
            for call in self.calls
        ), (route, self.calls)


def check_mcp(
    *, name: str, route: str, arguments: dict[str, Any], responses: dict[str, Any]
) -> None:
    """Invoke every advertised memory tool through its HTTP-backed handler."""
    for refuses in (False, True):
        probe = WireProbe(responses=responses, refuses=refuses)
        with probe.client() as client:
            server = EngineMcpServer(client=client, path_ingest=False)
            result = server.call_tool(name=name, arguments=arguments)
        probe.assert_route(route=route)
        assert result.get("isError") is refuses, (name, result)
        if refuses:
            assert "contract credential refused" in str(result), (name, result)


def check_cli(*, argv: list[str], route: str, responses: dict[str, Any]) -> None:
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
        probe.assert_route(route=route)
        assert code == int(refuses), (argv, code, stdout.getvalue(), stderr.getvalue())
        assert stdout.getvalue() or refuses or argv == ["operations", "list"], argv
        if refuses:
            assert (
                "contract credential refused" in stdout.getvalue() + stderr.getvalue()
            ), argv


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
    verify_clients(routes=set(operations), fixture=fixture)
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
