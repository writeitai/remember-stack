"""The dependency-light typed HTTP SDK (D62 client surface).

The SDK knows only the deployment API and typed wire values. It carries no
Postgres, worker, model-provider, or adapter dependency, so the base wheel can
be installed in an agent harness without installing the server runtime.
"""

from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
import mimetypes
from pathlib import Path
from typing import Literal
from typing import TypeVar
from uuid import UUID

import httpx
from pydantic import BaseModel
from pydantic import Field
from pydantic import SecretStr
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model.client import ConnectorCreate
from rememberstack.model.client import ConnectorDescriptor
from rememberstack.model.client import DeploymentBuildInfo
from rememberstack.model.client import PipelineReadinessReport
from rememberstack.model.client import ToolDescriptor
from rememberstack.model.documents import IngestedVersion
from rememberstack.model.envelope import Envelope

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ClientSettings(BaseSettings):
    """How a client reaches one deployment API."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    api_url: str = "http://127.0.0.1:8000"
    api_authorization: SecretStr | None = None
    api_timeout_seconds: float = Field(default=30.0, gt=0)


class MemoryApiError(Exception):
    """The deployment API was unreachable or returned an unusable response."""

    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(f"API {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class MemoryClient:
    """Typed synchronous client for query, ingest, and connector management."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        authorization: str | None = None,
        client: httpx.Client | None = None,
        timeout: float | None = None,
        settings: ClientSettings | None = None,
    ) -> None:
        """Bind either an owned HTTP client or an injected transport client."""
        if client is not None and any(
            value is not None for value in (base_url, authorization, timeout, settings)
        ):
            raise ValueError(
                "an injected client cannot be combined with client settings"
            )
        self._owned = client is None
        if client is not None:
            self._client = client
            return
        resolved = settings or ClientSettings.model_validate({})
        resolved_authorization = authorization or (
            resolved.api_authorization.get_secret_value()
            if resolved.api_authorization is not None
            else None
        )
        self._client = httpx.Client(
            base_url=base_url or resolved.api_url,
            headers=(
                {"Authorization": resolved_authorization}
                if resolved_authorization
                else None
            ),
            timeout=(timeout if timeout is not None else resolved.api_timeout_seconds),
        )

    @classmethod
    def from_settings(cls) -> "MemoryClient":
        """Build from the deployment API environment settings."""
        return cls(settings=ClientSettings.model_validate({}))

    def __enter__(self) -> "MemoryClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close only a transport the SDK created itself."""
        if self._owned:
            self._client.close()

    def recipes(self) -> tuple[ToolDescriptor, ...]:
        """Return the deployment recipe registry as typed tool descriptors."""
        payload = self._json("GET", "/recipes")
        if not isinstance(payload, list):
            raise MemoryApiError(
                status_code=200, detail="GET /recipes did not return a list"
            )
        return tuple(
            _validated(ToolDescriptor, item, endpoint="GET /recipes")
            for item in payload
        )

    def run_recipe(
        self, *, name: str, arguments: Mapping[str, object] | None = None
    ) -> Envelope:
        """Run one registry recipe and return its self-accounting envelope."""
        return _validated(
            Envelope,
            self._json("POST", f"/recipe/{name}", json_body=dict(arguments or {})),
            endpoint=f"POST /recipe/{name}",
        )

    def query_sql(
        self,
        *,
        sql: str,
        parameters: list[object] | tuple[object, ...] = (),
        max_rows: int | None = None,
    ) -> dict[str, object]:
        """Run one sandboxed SQL statement; returns QueryResult/v1 as a dict."""
        body: dict[str, object] = {"sql": sql, "parameters": list(parameters)}
        if max_rows is not None:
            body["max_rows"] = max_rows
        payload = self._json("POST", "/query/sql", json_body=body)
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200, detail="POST /query/sql did not return an object"
            )
        return payload

    def explain_sql(
        self, *, sql: str, parameters: list[object] | tuple[object, ...] = ()
    ) -> dict[str, object]:
        """EXPLAIN one SQL statement without executing it."""
        payload = self._json(
            "POST",
            "/query/sql/explain",
            json_body={"sql": sql, "parameters": list(parameters)},
        )
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200,
                detail="POST /query/sql/explain did not return an object",
            )
        return payload

    def query_cypher(
        self,
        *,
        cypher: str,
        parameters: Mapping[str, object] | None = None,
        max_rows: int | None = None,
        confirm: bool = False,
    ) -> dict[str, object]:
        """Run one read-only Cypher statement; returns QueryResult/v1 as a dict."""
        body: dict[str, object] = {
            "cypher": cypher,
            "parameters": dict(parameters or {}),
            "confirm": confirm,
        }
        if max_rows is not None:
            body["max_rows"] = max_rows
        payload = self._json("POST", "/query/cypher", json_body=body)
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200, detail="POST /query/cypher did not return an object"
            )
        return payload

    def explain_cypher(
        self, *, cypher: str, parameters: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Engine plan for one Cypher statement without executing it."""
        payload = self._json(
            "POST",
            "/query/cypher/explain",
            json_body={"cypher": cypher, "parameters": dict(parameters or {})},
        )
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200,
                detail="POST /query/cypher/explain did not return an object",
            )
        return payload

    def describe_query_space(
        self, *, pattern: str | None = None, include_examples: bool = False
    ) -> dict[str, object]:
        """Manifest-backed schema discovery."""
        params: dict[str, str | int] = {
            "include_examples": "true" if include_examples else "false"
        }
        if pattern is not None:
            params["pattern"] = pattern
        payload = self._json("GET", "/query/space", params=params)
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200, detail="GET /query/space did not return an object"
            )
        return payload

    def search_query_space(self, *, query: str, k: int = 10) -> list[dict[str, object]]:
        """Search checked-in manifest text only."""
        payload = self._json(
            "GET", "/query/space/search", params={"query": query, "k": k}
        )
        if not isinstance(payload, list):
            raise MemoryApiError(
                status_code=200, detail="GET /query/space/search did not return a list"
            )
        return [item for item in payload if isinstance(item, dict)]

    def list_saved_queries(
        self, *, namespace: str | None = None, status: str | None = None
    ) -> list[dict[str, object]]:
        """List saved-query registry metadata."""
        params: dict[str, str | int] = {}
        if namespace is not None:
            params["namespace"] = namespace
        if status is not None:
            params["status"] = status
        payload = self._json("GET", "/query/saved", params=params if params else None)
        if not isinstance(payload, list):
            raise MemoryApiError(
                status_code=200, detail="GET /query/saved did not return a list"
            )
        return [item for item in payload if isinstance(item, dict)]

    def describe_saved_query(
        self, *, namespace: str, name: str, version: int | None = None
    ) -> dict[str, object]:
        """Describe one saved-query version."""
        params: dict[str, str | int] = {}
        if version is not None:
            params["version"] = version
        payload = self._json(
            "GET", f"/query/saved/{namespace}/{name}", params=params if params else None
        )
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200,
                detail=f"GET /query/saved/{namespace}/{name} did not return an object",
            )
        return payload

    def run_saved_query(
        self,
        *,
        namespace: str,
        name: str,
        parameters: list[object] | tuple[object, ...] = (),
        version: int | None = None,
        max_rows: int | None = None,
    ) -> dict[str, object]:
        """Execute one active saved query; returns QueryResult/v1 as a dict."""
        body: dict[str, object] = {"parameters": list(parameters)}
        if version is not None:
            body["version"] = version
        if max_rows is not None:
            body["max_rows"] = max_rows
        payload = self._json(
            "POST", f"/query/saved/{namespace}/{name}/run", json_body=body
        )
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200,
                detail=(
                    f"POST /query/saved/{namespace}/{name}/run did not return an object"
                ),
            )
        return payload

    def call_open_query(self, *, name: str, arguments: Mapping[str, object]) -> object:
        """Dispatch one open-query infrastructure tool name through the HTTP API.

        Used by remote MCP so local and remote tools/list/call stay aligned
        without duplicating route knowledge in the transport loop. Arguments
        are validated strictly (same rules as local MCP) before the HTTP call.
        """
        from rememberstack.surfaces.query_sandbox.mcp_tools import (
            validate_open_query_arguments,
        )

        args = validate_open_query_arguments(name=name, arguments=arguments)
        if name == "query_sql":
            return self.query_sql(
                sql=str(args["sql"]),
                parameters=list(_sdk_param_list(args.get("parameters"))),
                max_rows=_optional_sdk_int(args.get("max_rows")),
            )
        if name == "explain_sql":
            return self.explain_sql(
                sql=str(args["sql"]),
                parameters=list(_sdk_param_list(args.get("parameters"))),
            )
        if name == "query_cypher":
            params = args.get("parameters")
            return self.query_cypher(
                cypher=str(args["cypher"]),
                parameters=params if isinstance(params, Mapping) else None,
                max_rows=_optional_sdk_int(args.get("max_rows")),
                confirm=bool(args.get("confirm", False)),
            )
        if name == "explain_cypher":
            params = args.get("parameters")
            return self.explain_cypher(
                cypher=str(args["cypher"]),
                parameters=params if isinstance(params, Mapping) else None,
            )
        if name == "describe_query_space":
            return self.describe_query_space(
                pattern=(
                    str(args["pattern"]) if args.get("pattern") is not None else None
                ),
                include_examples=bool(args.get("include_examples", False)),
            )
        if name == "search_query_space":
            k_value = _optional_sdk_int(args.get("k"))
            return self.search_query_space(
                query=str(args["query"]), k=10 if k_value is None else k_value
            )
        if name == "list_saved_queries":
            return self.list_saved_queries(
                namespace=(
                    str(args["namespace"])
                    if args.get("namespace") is not None
                    else None
                ),
                status=(
                    str(args["status"]) if args.get("status") is not None else None
                ),
            )
        if name == "describe_saved_query":
            return self.describe_saved_query(
                namespace=str(args["namespace"]),
                name=str(args["name"]),
                version=_optional_sdk_int(args.get("version")),
            )
        if name == "run_saved_query":
            return self.run_saved_query(
                namespace=str(args["namespace"]),
                name=str(args["name"]),
                version=_optional_sdk_int(args.get("version")),
                parameters=list(_sdk_param_list(args.get("parameters"))),
                max_rows=_optional_sdk_int(args.get("max_rows")),
            )
        raise ValueError(f"unknown open-query tool {name!r}")

    def resolve(
        self,
        *,
        name: str,
        entity_type: str | None = None,
        context_entity_ids: tuple[UUID, ...] = (),
    ) -> Envelope:
        """Resolve a name, optionally using bounded focal-entity context."""
        params: list[tuple[str, str]] = [("name", name)]
        if entity_type is not None:
            params.append(("entity_type", entity_type))
        params.extend(
            ("context_entity_ids", str(value)) for value in context_entity_ids
        )
        return _validated(
            Envelope,
            self._json("GET", "/resolve", params=tuple(params)),
            endpoint="GET /resolve",
        )

    def search_claims(
        self,
        *,
        query: str,
        k: int = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Search source claims; the returned envelope remains evidence grain."""
        return _validated(
            Envelope,
            self._json(
                "GET",
                "/search/claims",
                params={"query": query, "k": k, "channel": channel},
            ),
            endpoint="GET /search/claims",
        )

    def search_chunks(
        self,
        *,
        query: str,
        k: int = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Search live source passages as separately typed evidence."""
        return _validated(
            Envelope,
            self._json(
                "GET",
                "/search/chunks",
                params={"query": query, "k": k, "channel": channel},
            ),
            endpoint="GET /search/chunks",
        )

    def hydrate_relation(self, *, relation_id: UUID) -> Envelope:
        """Hydrate a relation through evidence to its source documents."""
        return _validated(
            Envelope,
            self._json("GET", f"/hydrate/relation/{relation_id}"),
            endpoint=f"GET /hydrate/relation/{relation_id}",
        )

    def deployment_build_info(self) -> DeploymentBuildInfo:
        """Read which code and model bindings are serving, before submitting work."""
        return _validated(
            DeploymentBuildInfo,
            self._json("GET", "/deployment"),
            endpoint="GET /deployment",
        )

    def pipeline_readiness(
        self, *, version_ids: tuple[UUID, ...], require_projections: bool = True
    ) -> PipelineReadinessReport:
        """Inspect exact continuous-stage and aggregate-projection readiness."""
        if not version_ids:
            raise ValueError("pipeline readiness requires at least one version_id")
        return _validated(
            PipelineReadinessReport,
            self._json(
                "POST",
                "/readiness",
                params={"require_projections": str(require_projections).lower()},
                json_body=[str(version_id) for version_id in version_ids],
            ),
            endpoint="POST /readiness",
        )

    def ingest(
        self,
        source: bytes | Path,
        *,
        filename: str | None = None,
        mime: str | None = None,
        title: str | None = None,
        source_kind: str | None = None,
        source_ref: str | None = None,
        source_modified_at: datetime | None = None,
        versioning_mode: Literal["snapshot", "living"] = "snapshot",
        source_version_ref: str | None = None,
    ) -> IngestedVersion:
        """Push bytes through E0, optionally as a stable document lineage.

        ``source_kind`` and ``source_ref`` are a pair. Reusing them creates a
        new immutable version of the same document when the bytes change.
        """
        if (source_kind is None) != (source_ref is None):
            raise ValueError("source_kind and source_ref must be supplied together")
        if source_kind is None and (
            source_modified_at is not None
            or source_version_ref is not None
            or versioning_mode != "snapshot"
        ):
            raise ValueError(
                "source timestamps, revisions, and living mode require"
                " source_kind/source_ref"
            )
        if source_modified_at is not None and (
            source_modified_at.tzinfo is None
            or source_modified_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("source_modified_at must be timezone-aware UTC")
        if isinstance(source, Path):
            content = source.read_bytes()
            filename = filename or source.name
            mime = mime or mimetypes.guess_type(source.name)[0]
        else:
            content = source
        if not filename:
            raise ValueError("filename is required when ingesting bytes")
        if not mime:
            mime = "application/octet-stream"
        params: dict[str, str] = {
            "filename": filename,
            "mime": mime,
            "versioning_mode": versioning_mode,
        }
        for key, value in (
            ("title", title),
            ("source_kind", source_kind),
            ("source_ref", source_ref),
            (
                "source_modified_at",
                source_modified_at.isoformat() if source_modified_at else None,
            ),
            ("source_version_ref", source_version_ref),
        ):
            if value is not None:
                params[key] = value
        return _validated(
            IngestedVersion,
            self._json(
                "POST",
                "/ingest",
                params=params,
                content=content,
                headers={"Content-Type": "application/octet-stream"},
            ),
            endpoint="POST /ingest",
        )

    def connectors(self) -> tuple[ConnectorDescriptor, ...]:
        """List deployment-side connectors without executing any client-side."""
        payload = self._json("GET", "/connectors")
        if not isinstance(payload, list):
            raise MemoryApiError(
                status_code=200, detail="GET /connectors did not return a list"
            )
        return tuple(
            _validated(ConnectorDescriptor, item, endpoint="GET /connectors")
            for item in payload
        )

    def add_connector(self, *, connector: ConnectorCreate) -> ConnectorDescriptor:
        """Create deployment-side connector configuration."""
        return _validated(
            ConnectorDescriptor,
            self._json(
                "POST", "/connectors", json_body=connector.model_dump(mode="json")
            ),
            endpoint="POST /connectors",
        )

    def pause_connector(self, *, connector_id: UUID) -> ConnectorDescriptor:
        """Pause connector execution in the deployment."""
        return _validated(
            ConnectorDescriptor,
            self._json("POST", f"/connectors/{connector_id}/pause"),
            endpoint=f"POST /connectors/{connector_id}/pause",
        )

    def connector_status(self, *, connector_id: UUID) -> ConnectorDescriptor:
        """Return one connector's deployment-side status."""
        return _validated(
            ConnectorDescriptor,
            self._json("GET", f"/connectors/{connector_id}"),
            endpoint=f"GET /connectors/{connector_id}",
        )

    def _json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | tuple[tuple[str, str], ...] | None = None,
        json_body: object | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> object:
        """Send one request, map typed HTTP failure, and decode JSON."""
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                content=content,
                headers=headers,
            )
        except httpx.HTTPError as error:
            raise MemoryApiError(status_code=0, detail=str(error)) from error
        if not response.is_success:
            detail = response.text
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and "detail" in body:
                detail = str(body["detail"])
            raise MemoryApiError(status_code=response.status_code, detail=detail)
        try:
            return response.json()
        except ValueError as error:
            raise MemoryApiError(
                status_code=response.status_code,
                detail=f"{method} {path} returned invalid JSON",
            ) from error


def _optional_sdk_int(value: object) -> int | None:
    """Coerce an optional integer argument without treating bool as int."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected an integer")
    return value


def _sdk_param_list(value: object) -> list[object]:
    """Coerce optional positional parameters to a list for SQL tools."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    raise ValueError("parameters must be a JSON array")


def _validated(model: type[_ModelT], payload: object, *, endpoint: str) -> _ModelT:
    """Validate one JSON-mode response or raise the SDK's public error type."""
    try:
        return model.model_validate(payload, strict=False)
    except (ValidationError, TypeError) as error:
        raise MemoryApiError(
            status_code=200, detail=f"{endpoint} returned an invalid response body"
        ) from error
