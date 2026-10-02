#!/usr/bin/env python3
"""Export D140 SDK contracts from same-revision Python and offline OpenAPI.

Generation is offline. --check compares bytes without authoring tracked files.
The normative inventory remains reviewed input; source evolution fails closed.
"""

from __future__ import annotations

import argparse
import ast
import copy
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

from export_openapi import build_document
from pydantic import BaseModel
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages/typescript-client"


def source_tree(*, module: str) -> ast.Module:
    """Parse a source module without executing it for inventory checks."""
    return ast.parse((ROOT / "src" / (module.replace(".", "/") + ".py")).read_text())


def function_signature(
    *, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> dict[str, Any]:
    """Record exact source annotations, argument defaults and return types."""
    return {
        "arguments": ast.unparse(node.args),
        "returns": ast.unparse(node.returns) if node.returns else None,
    }


def source_exports(*, module: str) -> list[str]:
    """Read the literal __all__ contract; non-literal changes require review."""
    tree = source_tree(module=module)
    return list(
        next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
        )
    )


def verify_inventory() -> dict[str, Any]:
    """Refuse any Python surface change missing from the reviewed manifest."""
    inventory = json.loads(
        (ROOT / "plan/designs/typescript_client_parity.json").read_text()
    )
    if sorted(source_exports(module="remember.__init__")) != inventory["exports"]:
        raise ValueError(
            "Python root exports drifted from the normative parity inventory"
        )
    tree = source_tree(module="remember.client")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in inventory["classes"]:
            methods = {
                method.name: {
                    **function_signature(node=method),
                    "property": any(
                        isinstance(dec, ast.Name) and dec.id == "property"
                        for dec in method.decorator_list
                    ),
                }
                for method in node.body
                if isinstance(method, ast.FunctionDef)
                and (not method.name.startswith("_") or method.name == "__init__")
            }
            expected = {
                name: {
                    key: value
                    for key, value in signature.items()
                    if key != "typescriptOptions"
                }
                for name, signature in inventory["classes"][node.name].items()
            }
            if methods != expected:
                raise ValueError(
                    f"Python {node.name} signatures drifted from the normative inventory"
                )
    resolver = next(
        node
        for node in source_tree(module="remember.connection").body
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_connection"
    )
    for key, value in function_signature(node=resolver).items():
        if inventory["resolve_connection"][key] != value:
            raise ValueError("resolve_connection signature drifted")
    query_wrapper = next(
        node
        for node in source_tree(module="remember.models").body
        if isinstance(node, ast.ClassDef) and node.name == "QueryResultDict"
    )
    properties = [
        node.name
        for node in query_wrapper.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(dec, ast.Name) and dec.id == "property"
            for dec in node.decorator_list
        )
    ]
    if properties != inventory["QueryResultDict"]:
        raise ValueError("QueryResultDict properties drifted")
    support = inventory["supportExports"]
    if set(source_exports(module="remember.mcp_tools.__init__")) != set(
        support["memoryToolCatalogue"]["exports"]
    ):
        raise ValueError("MCP support exports drifted")
    for qualified, expected in support["functions"].items():
        module, name = qualified.rsplit(".", 1)
        node = next(
            node
            for node in source_tree(module=module).body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        for key, value in function_signature(node=node).items():
            if expected[key] != value:
                raise ValueError(f"{qualified} signature drifted")
    for qualified, expected in support["types"].items():
        module, name = qualified.rsplit(".", 1)
        node = next(
            node
            for node in source_tree(module=module).body
            if isinstance(node, ast.ClassDef) and node.name == name
        )
        fields = {
            field.target.id: {
                "annotation": ast.unparse(field.annotation),
                "default": ast.unparse(field.value) if field.value else None,
            }
            for field in node.body
            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)
        }
        if fields != expected["fields"]:
            raise ValueError(f"{qualified} fields drifted")
        methods = {
            method.name: function_signature(node=method)
            for method in node.body
            if isinstance(method, ast.FunctionDef)
            and (not method.name.startswith("_") or method.name == "__init__")
        }
        recorded = {
            name: {key: value for key, value in method.items() if key != "typescript"}
            for name, method in expected["methods"].items()
        }
        if methods != recorded:
            raise ValueError(f"{qualified} methods drifted")
    return inventory


def reference_tree(*, value: Any, prefix: str) -> Any:
    """Retarget local schema definitions while preserving the schema dialect."""
    if isinstance(value, list):
        return [reference_tree(value=item, prefix=prefix) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: (
            prefix + item.rsplit("/", 1)[1]
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/")
            else reference_tree(value=item, prefix=prefix)
        )
        for key, item in value.items()
    }


def client_models() -> dict[str, Any]:
    """Export all client models and their private discovery response contracts."""
    models: dict[str, Any] = {}
    classes: dict[str, type[BaseModel]] = {}
    modules = [
        "remember.models",
        "remember.query_sandbox.result",
        "remember.client",
        "remember.issuer",
        "remember.connection",
        "remember.credentials",
    ]
    for module_name in modules:
        module = importlib.import_module(module_name)
        for name, model in vars(module).items():
            if (
                isinstance(model, type)
                and issubclass(model, BaseModel)
                and model is not BaseModel
            ):
                if (
                    model.__module__ not in modules
                    or name.startswith("_Environment")
                    or name == "_ConfigDirSettings"
                ):
                    continue
                schema = model.model_json_schema()
                models.update(schema.pop("$defs", {}))
                models[name.lstrip("_")] = schema
                classes[name.lstrip("_")] = model
    for name, model in classes.items():
        models[name]["x-extra"] = model.model_config.get("extra", "ignore")
        for field_name, field in model.model_fields.items():
            if field.exclude:
                models[name]["properties"][field.alias or field_name]["x-exclude"] = (
                    True
                )
            if not field.is_required():
                models[name]["properties"][field.alias or field_name]["default"] = (
                    to_jsonable_python(field.get_default(call_default_factory=True))
                )
        tree = source_tree(module=model.__module__)
        definition = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == model.__name__
        )
        for node in definition.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if "UTCDateTime" in ast.unparse(node.annotation):
                    field = model.model_fields[node.target.id]
                    models[name]["properties"][field.alias or node.target.id][
                        "x-utc"
                    ] = True
    from remember.models import ClaimValidPrecision
    from remember.models import DocumentStatusFilter
    from remember.models import TemporalMatch

    for name, model in {
        "ClaimValidPrecision": ClaimValidPrecision,
        "TemporalMatch": TemporalMatch,
        "DocumentStatusFilter": DocumentStatusFilter,
    }.items():
        schema = TypeAdapter(model).json_schema()
        models.update(schema.pop("$defs", {}))
        if schema != {"$ref": "#/$defs/" + name}:
            models[name] = schema
    # A named recursive JSON type keeps open object APIs typed without DTO coupling.
    from pydantic import JsonValue

    schema = TypeAdapter(JsonValue).json_schema()
    models.update(schema.pop("$defs", {}))
    # Pydantic exports JsonValue as {}, which loses its recursive JSON typing.
    # JSON parsing/serialization admits exactly these six JSON categories.
    models["JsonValue"] = {
        "anyOf": [
            {"type": "object", "additionalProperties": {"$ref": "#/$defs/JsonValue"}},
            {"type": "array", "items": {"$ref": "#/$defs/JsonValue"}},
            {"type": "string"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "null"},
        ]
    }
    return models


def read_routes() -> list[dict[str, str]]:
    """Export the route-scope authority, not the separate spend gate table."""
    from rememberstack.surfaces.route_scope import _READ_ROUTES

    return [
        {"method": method, "pattern": pattern.pattern}
        for method, pattern in _READ_ROUTES
    ]


def serialize(*, value: Any) -> str:
    """Produce stable JSON artifacts for review and byte-for-byte drift checks."""
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def artifacts() -> dict[Path, str]:
    """Build SDK and public provider artifacts without changing tracked files."""
    from remember.client import _QUERY_ERROR_HTTP_STATUS
    from remember.mcp_tools import memory_tools
    from remember.mcp_tools import PROJECT_ARGUMENT
    from remember.mcp_tools._definitions import _INGEST_INPUT_SCHEMA_WITHOUT_PATH
    from remember.mime import KNOWN_UPLOAD_MIME_TYPES
    from remember.models import _SECRET_CONFIGURATION_KEYS

    inventory = verify_inventory()
    document = build_document()
    committed = json.loads((ROOT / "openapi.json").read_text())
    if document != committed:
        raise ValueError(
            "offline API export differs from openapi.json; regenerate the engine schema first"
        )
    schemas = client_models()
    runtime = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": schemas,
    }
    type_document = copy.deepcopy(document)
    components = type_document["components"]["schemas"]
    for name, schema in schemas.items():
        components[name] = reference_tree(value=schema, prefix="#/components/schemas/")
        output = copy.deepcopy(components[name])
        if output.get("type") == "object":
            output["required"] = sorted(output.get("properties", {}))
        components["Output" + name] = output
    # Output references use output models so nested defaults remain non-optional.
    for name in schemas:
        components["Output" + name] = output_references(
            value=components["Output" + name], names=set(schemas)
        )
    routes: dict[str, Any] = {}
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            routes[method.upper() + " " + path] = {
                "method": method.upper(),
                "path": path,
                "operationId": operation.get("operationId"),
                "parameters": operation.get("parameters", []),
                "requestBody": operation.get("requestBody"),
                "responses": operation.get("responses", {}),
            }
    # Optional connector routes are source-backed and never added to served OpenAPI.
    for method, path in [
        ("GET", "/connectors"),
        ("POST", "/connectors"),
        ("POST", "/connectors/{connector_id}/pause"),
        ("GET", "/connectors/{connector_id}"),
    ]:
        if path not in (ROOT / "src/remember/client.py").read_text():
            raise ValueError(
                f"optional connector route no longer appears in Python: {path}"
            )
        routes[method + " " + path] = {
            "method": method,
            "path": path,
            "optionalProfile": "connectors",
        }
    provider_names = {"IssuerMetadata", "ResolvedProject"}
    provider = {
        "$schema": runtime["$schema"],
        "contracts": {name: schemas[name] for name in sorted(provider_names)},
    }
    provider["contracts"]["Whoami"] = {"type": "object", "additionalProperties": True}
    tools = [
        {**dataclasses.asdict(tool), "annotations": tool.annotations}
        for tool in memory_tools()
    ]
    source_files = sorted(
        (ROOT / "src/remember").rglob("*.py"),
        key=lambda path: path.relative_to(ROOT).as_posix(),
    )
    source_hash = hashlib.sha256(
        b"".join(
            path.relative_to(ROOT).as_posix().encode()
            + b"\0"
            + path.read_text(encoding="utf-8").encode()
            for path in source_files
        )
    ).hexdigest()
    metadata = {
        "publishedBaseline": inventory["publishedBaseline"],
        "pythonSourceRevision": inventory["pythonSourceRevision"],
        "sourceHash": source_hash,
        "openapiHash": hashlib.sha256(serialize(value=document).encode()).hexdigest(),
    }
    return {
        PACKAGE / "contracts/schemas.json": serialize(value=runtime),
        PACKAGE / "contracts/openapi-types.json": serialize(value=type_document),
        PACKAGE / "contracts/routes.json": serialize(value=routes),
        PACKAGE / "contracts/catalogue.json": serialize(
            value={
                "tools": tools,
                "projectArgument": PROJECT_ARGUMENT,
                "ingestWithoutPath": _INGEST_INPUT_SCHEMA_WITHOUT_PATH,
            }
        ),
        PACKAGE / "contracts/constants.json": serialize(
            value={
                "queryErrorStatus": _QUERY_ERROR_HTTP_STATUS,
                "mime": KNOWN_UPLOAD_MIME_TYPES,
                "readRoutes": read_routes(),
                "secretConfigurationKeys": sorted(_SECRET_CONFIGURATION_KEYS),
                "modelValidators": {
                    node.name: [
                        method.name
                        for method in node.body
                        if isinstance(method, ast.FunctionDef)
                        and any(
                            isinstance(decorator, ast.Call)
                            and isinstance(decorator.func, ast.Name)
                            and decorator.func.id
                            in {"model_validator", "field_validator"}
                            for decorator in method.decorator_list
                        )
                    ]
                    for node in source_tree(module="remember.models").body
                    if isinstance(node, ast.ClassDef)
                    and any(
                        isinstance(method, ast.FunctionDef)
                        and any(
                            isinstance(decorator, ast.Call)
                            and isinstance(decorator.func, ast.Name)
                            and decorator.func.id
                            in {"model_validator", "field_validator"}
                            for decorator in method.decorator_list
                        )
                        for method in node.body
                    )
                },
            }
        ),
        PACKAGE / "contracts/parity.json": serialize(value=inventory),
        PACKAGE / "contracts/metadata.json": serialize(value=metadata),
        ROOT / "contracts/issuer-provider.json": serialize(value=provider),
    }


def output_references(*, value: Any, names: set[str]) -> Any:
    """Make nested output model references reflect recursively applied defaults."""
    if isinstance(value, list):
        return [output_references(value=item, names=names) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "$ref" and isinstance(item, str) and item.rsplit("/", 1)[-1] in names:
            item = "#/components/schemas/Output" + item.rsplit("/", 1)[-1]
        result[key] = output_references(value=item, names=names)
    return result


def main() -> int:
    """Write contracts or fail on drift with the exact stale artifact path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, content in artifacts().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        raise SystemExit("TypeScript contracts drifted: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
