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
from typing import Final
from typing import Literal
from typing import TypeVar
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
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
from rememberstack.model.envelope import ContextBundleV1
from rememberstack.model.envelope import Envelope
from rememberstack.surfaces.query_sandbox.result import QueryResult

_ModelT = TypeVar("_ModelT", bound=BaseModel)

_QUERY_ERROR_HTTP_STATUS: Final[dict[str, int]] = {
    "saved_query_not_found": 404,
    "p2_unavailable": 404,
    "saved_query_disabled": 409,
    "saved_query_revalidation_pending": 409,
    "saved_query_incompatible": 409,
    "quota_exceeded": 409,
    "concurrency_exceeded": 409,
    "schema_version_mismatch": 409,
    "pg_unavailable": 503,
    "p1_unavailable": 503,
    "corpus_body_unavailable": 503,
    "generation_unavailable": 503,
    "statement_timeout": 500,
    "lock_timeout": 500,
    "cancelled": 500,
    "resource_limit": 500,
    "execution_error": 500,
    "confirmation_failed": 500,
    "parse_error": 422,
    "multiple_statements": 422,
    "statement_not_allowed": 422,
    "relation_not_allowed": 422,
    "function_not_allowed": 422,
    "function_placement_not_allowed": 422,
    "operator_not_allowed": 422,
    "invalid_parameter": 422,
    "unbounded_recursion": 422,
    "cypher_parse_error": 422,
    "cypher_not_allowed": 422,
}


class ClientSettings(BaseSettings):
    """How a client reaches one deployment API."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    api_url: str = "http://127.0.0.1:8000"
    api_authorization: SecretStr | None = None
    api_timeout_seconds: float = Field(default=30.0, gt=0)


class MemoryApiError(Exception):
    """The deployment API was unreachable or returned an unusable response."""

    def __init__(
        self, *, status_code: int, detail: str, code: str | None = None
    ) -> None:
        super().__init__(f"API {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.code = code


class _DiscoveryHit(BaseModel):
    """Exact wire contract for one query-space search result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["view", "function", "core_operation", "example"]
    name: str = Field(min_length=1)
    score: float
    purpose: str
    tags: tuple[str, ...]


class _SavedQuerySummary(BaseModel):
    """Exact wire contract for one saved-query discovery row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query_id: UUID
    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: str = Field(min_length=1)
    description: str | None
    origin: str = Field(min_length=1)
    assurance: str | None
    query_hash: str = Field(min_length=1)
    validated_surface_manifest_hash: str = Field(min_length=1)


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

    def list_operations(self) -> tuple[ToolDescriptor, ...]:
        """Return the deployment's four assured-operation descriptors."""
        payload = self._json("GET", "/operations")
        if not isinstance(payload, list):
            raise MemoryApiError(
                status_code=200, detail="GET /operations did not return a list"
            )
        return tuple(
            _validated(ToolDescriptor, item, endpoint="GET /operations")
            for item in payload
        )

    def run_operation(
        self, *, name: str, arguments: Mapping[str, object] | None = None
    ) -> Envelope | ContextBundleV1:
        """Run one assured operation and validate its exact wire contract."""
        path = f"/operations/{quote(name, safe='')}"
        endpoint = f"POST {path}"
        payload = self._json("POST", path, json_body=dict(arguments or {}))
        if isinstance(payload, dict) and payload.get("contract") == "ContextBundle/v1":
            return _validated(ContextBundleV1, payload, endpoint=endpoint)
        return _validated(Envelope, payload, endpoint=endpoint)

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
        return _validated_dict(
            QueryResult,
            self._json("POST", "/query/sql", json_body=body),
            endpoint="POST /query/sql",
        )

    def explain_sql(
        self, *, sql: str, parameters: list[object] | tuple[object, ...] = ()
    ) -> dict[str, object]:
        """EXPLAIN one SQL statement without executing it."""
        return _validated_dict(
            QueryResult,
            self._json(
                "POST",
                "/query/sql/explain",
                json_body={"sql": sql, "parameters": list(parameters)},
            ),
            endpoint="POST /query/sql/explain",
        )

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
        return _validated_dict(
            QueryResult,
            self._json("POST", "/query/cypher", json_body=body),
            endpoint="POST /query/cypher",
        )

    def explain_cypher(
        self, *, cypher: str, parameters: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Engine plan for one Cypher statement without executing it."""
        return _validated_dict(
            QueryResult,
            self._json(
                "POST",
                "/query/cypher/explain",
                json_body={"cypher": cypher, "parameters": dict(parameters or {})},
            ),
            endpoint="POST /query/cypher/explain",
        )

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
        return _validated_list(
            _DiscoveryHit,
            self._json("GET", "/query/space/search", params={"query": query, "k": k}),
            endpoint="GET /query/space/search",
        )

    def list_saved_queries(
        self, *, namespace: str | None = None, status: str | None = None
    ) -> list[dict[str, object]]:
        """List saved-query registry metadata."""
        params: dict[str, str | int] = {}
        if namespace is not None:
            params["namespace"] = namespace
        if status is not None:
            params["status"] = status
        return _validated_list(
            _SavedQuerySummary,
            self._json("GET", "/query/saved", params=params if params else None),
            endpoint="GET /query/saved",
        )

    def describe_saved_query(
        self, *, namespace: str, name: str, version: int | None = None
    ) -> dict[str, object]:
        """Describe one saved-query version."""
        namespace_path = _saved_query_path_segment(value=namespace, field="namespace")
        name_path = _saved_query_path_segment(value=name, field="name")
        params: dict[str, str | int] = {}
        if version is not None:
            params["version"] = version
        payload = self._json(
            "GET",
            f"/query/saved/{namespace_path}/{name_path}",
            params=params if params else None,
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
        namespace_path = _saved_query_path_segment(value=namespace, field="namespace")
        name_path = _saved_query_path_segment(value=name, field="name")
        body: dict[str, object] = {"parameters": list(parameters)}
        if version is not None:
            body["version"] = version
        if max_rows is not None:
            body["max_rows"] = max_rows
        path = f"/query/saved/{namespace_path}/{name_path}/run"
        endpoint = f"POST {path}"
        return _validated_dict(
            QueryResult, self._json("POST", path, json_body=body), endpoint=endpoint
        )

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
        context_entity_ids: tuple[UUID, ...] = (),
    ) -> Envelope:
        """Resolve a name, optionally using bounded focal-entity context."""
        params: list[tuple[str, str]] = [("name", name)]
        params.extend(
            ("context_entity_ids", str(value)) for value in context_entity_ids
        )
        return _validated(
            Envelope,
            self._json("GET", "/resolve", params=tuple(params)),
            endpoint="GET /resolve",
        )

    def lookup_relations(
        self,
        *,
        subject_entity_id: UUID | None = None,
        predicate: str | None = None,
        object_entity_id: UUID | None = None,
        valid_at: datetime | None = None,
    ) -> Envelope:
        """Read current or valid-time relations matching an optional pattern."""
        params: dict[str, str] = {}
        if subject_entity_id is not None:
            params["subject_entity_id"] = str(subject_entity_id)
        if predicate is not None:
            params["predicate"] = predicate
        if object_entity_id is not None:
            params["object_entity_id"] = str(object_entity_id)
        if valid_at is not None:
            params["valid_at"] = valid_at.isoformat()
        return _validated(
            Envelope,
            self._json("GET", "/lookup/relations", params=params if params else None),
            endpoint="GET /lookup/relations",
        )

    def transcript_relation(self, *, relation_id: UUID) -> Envelope:
        """Read the bounded decision transcript for one relation."""
        return _validated(
            Envelope,
            self._json("GET", f"/transcript/relation/{relation_id}"),
            endpoint=f"GET /transcript/relation/{relation_id}",
        )

    def lookup_observations(
        self, *, entity_id: UUID, property_query: str | None = None, k: int = 10
    ) -> Envelope:
        """Read live observations for one entity, optionally by property text."""
        params: dict[str, str | int] = {"entity_id": str(entity_id), "k": k}
        if property_query is not None:
            params["property_query"] = property_query
        return _validated(
            Envelope,
            self._json("GET", "/lookup/observations", params=params),
            endpoint="GET /lookup/observations",
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
            code: str | None = None
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and set(body) == {"detail"}:
                public_detail = body["detail"]
                if isinstance(public_detail, dict):
                    structured = (
                        _structured_query_error(
                            detail=public_detail, status_code=response.status_code
                        )
                        if path.startswith("/query/")
                        else None
                    )
                    if structured is not None:
                        code, detail = structured
                    elif path.startswith("/query/"):
                        detail = "deployment API returned a malformed structured error"
                    else:
                        detail = str(public_detail)
                else:
                    detail = str(public_detail)
            elif isinstance(body, dict) and "detail" in body:
                detail = "deployment API returned a malformed error envelope"
            raise MemoryApiError(
                status_code=response.status_code, detail=detail, code=code
            )
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


def _saved_query_path_segment(*, value: str, field: str) -> str:
    """Validate a registry identifier before encoding it as one URL segment."""
    from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
    from rememberstack.surfaces.query_sandbox.mcp_tools import (
        validate_saved_query_identifier,
    )

    try:
        validated = validate_saved_query_identifier(value=value, field=field)
    except SandboxRejection as error:
        raise ValueError(error.message) from error
    return quote(validated, safe="")


def _structured_query_error(
    *, detail: dict[object, object], status_code: int
) -> tuple[str, str] | None:
    """Accept only the complete public query-error shape at its bound HTTP status."""
    if set(detail) != {"code", "message"}:
        return None
    code = detail.get("code")
    message = detail.get("message")
    if not isinstance(code, str) or not isinstance(message, str) or not message:
        return None
    if _QUERY_ERROR_HTTP_STATUS.get(code) != status_code:
        return None
    return code, message


def _validated_list(
    model: type[_ModelT], payload: object, *, endpoint: str
) -> list[dict[str, object]]:
    """Validate every member of one list atomically against its wire contract."""
    if not isinstance(payload, list):
        raise MemoryApiError(
            status_code=200, detail=f"{endpoint} returned an invalid response body"
        )
    try:
        return [
            item.model_dump(mode="json") for item in map(model.model_validate, payload)
        ]
    except (ValidationError, TypeError) as error:
        raise MemoryApiError(
            status_code=200, detail=f"{endpoint} returned an invalid response body"
        ) from error


def _validated_dict(
    model: type[_ModelT], payload: object, *, endpoint: str
) -> dict[str, object]:
    """Validate and JSON-render one object-shaped public wire response."""
    return _validated(model, payload, endpoint=endpoint).model_dump(mode="json")


def _validated(model: type[_ModelT], payload: object, *, endpoint: str) -> _ModelT:
    """Validate one JSON-mode response or raise the SDK's public error type."""
    try:
        return model.model_validate(payload, strict=False)
    except (ValidationError, TypeError) as error:
        raise MemoryApiError(
            status_code=200, detail=f"{endpoint} returned an invalid response body"
        ) from error
