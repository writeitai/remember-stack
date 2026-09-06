"""The remember.dev clients (D53/D62/D65).

- :class:: Ergonomic memory client connecting directly to tenant deployment
  ingress for memory storage and retrieval (D65).
- :class:: Core typed synchronous client for memory operations.
- :class:: Control-plane client for organisation status, deployment
  inspection, and billing balances (D53).
"""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
import mimetypes
from pathlib import Path
import time
from types import TracebackType
from typing import Any
from typing import Final
from typing import Literal
from typing import Self
from typing import TypeVar
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import AliasChoices
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from remember.errors import CloudError
from remember.errors import MemoryApiError
from remember.errors import NotPermitted
from remember.errors import RateLimited
from remember.errors import Unauthenticated
from remember.models import BillingStatus
from remember.models import ConnectorCreate
from remember.models import ConnectorDescriptor
from remember.models import ContextBundleV1
from remember.models import Deployment
from remember.models import DeploymentBuildInfo
from remember.models import Envelope
from remember.models import IngestedVersion
from remember.models import LedgerEntry
from remember.models import PipelineReadinessReport
from remember.models import QueryResultDict
from remember.models import ReadinessRequirements
from remember.models import SpendGate
from remember.models import ToolDescriptor
from remember.query_sandbox.result import QueryResult

_ModelT = TypeVar("_ModelT", bound=BaseModel)

_QUERY_ERROR_HTTP_STATUS: Final[dict[str, int]] = {
    "saved_query_not_found": 404,
    "saved_query_disabled": 409,
    "saved_query_revalidation_pending": 409,
    "saved_query_incompatible": 409,
    "quota_exceeded": 409,
    "concurrency_exceeded": 409,
    "schema_version_mismatch": 409,
    "pg_unavailable": 503,
    "p1_unavailable": 503,
    "graph_unavailable": 503,
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
}


class ClientSettings(BaseSettings):
    """How a client reaches one deployment API."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_", extra="ignore")

    api_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias=AliasChoices(
            "REMEMBER_DATA_PLANE_URL",
            "REMEMBER_API_URL",
            "REMEMBERSTACK_API_URL",
            "api_url",
        ),
    )
    api_authorization: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REMEMBER_API_KEY",
            "REMEMBER_TOKEN",
            "REMEMBER_API_AUTHORIZATION",
            "REMEMBERSTACK_API_AUTHORIZATION",
            "api_authorization",
        ),
    )
    api_timeout_seconds: float = Field(default=30.0, gt=0)


class ExplicitEnvSettings(BaseSettings):
    """Explicit environment overrides for client data-plane connectivity."""

    model_config = SettingsConfigDict(extra="ignore")

    data_plane_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REMEMBER_DATA_PLANE_URL", "REMEMBER_API_URL", "REMEMBERSTACK_API_URL"
        ),
    )


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
        api_url: str | None = None,
        data_plane_url: str | None = None,
        token: str | None = None,
        authorization: str | None = None,
        client: httpx.Client | None = None,
        timeout: float | None = None,
        settings: ClientSettings | None = None,
    ) -> None:
        """Bind either an owned HTTP client or an injected transport client."""
        if client is not None and any(
            value is not None
            for value in (
                base_url,
                api_url,
                data_plane_url,
                token,
                authorization,
                timeout,
                settings,
            )
        ):
            raise ValueError(
                "an injected client cannot be combined with client settings"
            )
        self._owned = client is None
        if client is not None:
            self._client = client
            return
        resolved = settings or ClientSettings.model_validate({})
        raw_auth = (
            authorization
            or token
            or (
                resolved.api_authorization.get_secret_value()
                if resolved.api_authorization is not None
                else None
            )
        )
        env_settings = ExplicitEnvSettings.model_validate({})
        env_url = env_settings.data_plane_url
        explicit_url = data_plane_url or base_url or api_url or env_url
        resolved_url = explicit_url or resolved.api_url

        resolved_authorization = None
        if raw_auth:
            resolved_authorization = (
                raw_auth if raw_auth.startswith("Bearer ") else f"Bearer {raw_auth}"
            )

        self._client = httpx.Client(
            base_url=resolved_url,
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

    def open_query(
        self,
        sql: str,
        *,
        parameters: Sequence[object] = (),
        max_rows: int | None = None,
    ) -> QueryResultDict:
        """Run one sandboxed SQL statement; returns QueryResultDict with .rows attribute."""
        res = self.query_sql(sql=sql, parameters=list(parameters), max_rows=max_rows)
        return QueryResultDict(res)

    def explain_query(
        self, sql: str, *, parameters: Sequence[object] = ()
    ) -> QueryResultDict:
        """EXPLAIN one SQL statement without executing it."""
        res = self.explain_sql(sql=sql, parameters=list(parameters))
        return QueryResultDict(res)

    def fact_context(
        self,
        query: str,
        *,
        time: Mapping[str, object] | None = None,
        hops: int | None = None,
        predicate: str | None = None,
        entity_ids: Sequence[str | UUID] | None = None,
    ) -> Envelope:
        """Run the assured fact_context operation."""
        args: dict[str, object] = {"query": query}
        if time is not None:
            args["time"] = dict(time)
        if hops is not None:
            args["hops"] = hops
        if predicate is not None:
            args["predicate"] = predicate
        if entity_ids is not None:
            args["entity_ids"] = [str(e) for e in entity_ids]
        res = self.run_operation(name="fact_context", arguments=args)
        assert isinstance(res, Envelope)
        return res

    def answer_context(
        self, query: str, *, time: Mapping[str, object] | None = None
    ) -> ContextBundleV1:
        """Run the assured answer_context operation."""
        args: dict[str, object] = {"query": query}
        if time is not None:
            args["time"] = dict(time)
        res = self.run_operation(name="answer_context", arguments=args)
        assert isinstance(res, ContextBundleV1)
        return res

    def testimony_context(self, query: str) -> Envelope:
        """Run the assured testimony_context operation."""
        res = self.run_operation(name="testimony_context", arguments={"query": query})
        assert isinstance(res, Envelope)
        return res

    def resolve_entity(self, name: str) -> Envelope:
        """Run the assured resolve_entity operation."""
        res = self.run_operation(name="resolve_entity", arguments={"name": name})
        assert isinstance(res, Envelope)
        return res

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
        from remember.query_sandbox.mcp_tools import validate_open_query_arguments

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
        self, *, name: str, context_entity_ids: tuple[UUID, ...] = ()
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

    def graph_neighborhood(
        self,
        *,
        entity_id: UUID,
        hops: int = 2,
        predicates: tuple[str, ...] = (),
        valid_at: datetime | None = None,
        believed_at: datetime | None = None,
        limit: int = 500,
        continuation: str | None = None,
        include_paths: bool = False,
    ) -> Envelope:
        """Return a typed bounded current or bitemporal neighborhood."""
        return _validated(
            Envelope,
            self._json(
                "POST",
                "/graph/neighborhood",
                json_body={
                    "entity_id": str(entity_id),
                    "hops": hops,
                    "predicates": list(predicates),
                    "valid_at": valid_at.isoformat() if valid_at is not None else None,
                    "believed_at": (
                        believed_at.isoformat() if believed_at is not None else None
                    ),
                    "limit": limit,
                    "continuation": continuation,
                    "include_paths": include_paths,
                },
            ),
            endpoint="POST /graph/neighborhood",
        )

    def graph_path(
        self,
        *,
        from_entity_id: UUID,
        to_entity_id: UUID,
        max_hops: int = 4,
        predicates: tuple[str, ...] = (),
        valid_at: datetime | None = None,
        believed_at: datetime | None = None,
    ) -> Envelope:
        """Return bounded equal-length shortest paths between two entities."""
        return _validated(
            Envelope,
            self._json(
                "POST",
                "/graph/path",
                json_body={
                    "from_entity_id": str(from_entity_id),
                    "to_entity_id": str(to_entity_id),
                    "max_hops": max_hops,
                    "predicates": list(predicates),
                    "valid_at": valid_at.isoformat() if valid_at is not None else None,
                    "believed_at": (
                        believed_at.isoformat() if believed_at is not None else None
                    ),
                },
            ),
            endpoint="POST /graph/path",
        )

    def graph_citation_path(
        self, *, from_doc_id: UUID, to_doc_id: UUID, max_hops: int = 6
    ) -> Envelope:
        """Return bounded directed citation paths between two documents."""
        return _validated(
            Envelope,
            self._json(
                "POST",
                "/graph/citation-path",
                json_body={
                    "from_doc_id": str(from_doc_id),
                    "to_doc_id": str(to_doc_id),
                    "max_hops": max_hops,
                },
            ),
            endpoint="POST /graph/citation-path",
        )

    def deployment_build_info(self) -> DeploymentBuildInfo:
        """Read which code and model bindings are serving, before submitting work."""
        return _validated(
            DeploymentBuildInfo,
            self._json("GET", "/deployment"),
            endpoint="GET /deployment",
        )

    def pipeline_readiness(
        self, *, version_ids: tuple[UUID, ...], require: ReadinessRequirements
    ) -> PipelineReadinessReport:
        """Inspect exact pipeline and explicitly requested capabilities."""
        if not version_ids:
            raise ValueError("pipeline readiness requires at least one version_id")
        return _validated(
            PipelineReadinessReport,
            self._json(
                "POST",
                "/readiness",
                json_body={
                    "version_ids": [str(version_id) for version_id in version_ids],
                    "require": require.model_dump(mode="json"),
                },
            ),
            endpoint="POST /readiness",
        )

    def wait_for_readiness(
        self,
        version_ids: Sequence[str | UUID],
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
        require_p3: bool = False,
    ) -> PipelineReadinessReport:
        """Poll /readiness until all requested version_ids are ready or timeout expires."""
        start = time.monotonic()
        req_ids = [UUID(str(v)) for v in version_ids]
        require = ReadinessRequirements(
            pipeline=True, p1=True, live_graph=True, p3=require_p3
        )
        while True:
            report = self.pipeline_readiness(
                version_ids=tuple(req_ids), require=require
            )
            if report.ready:
                return report
            if time.monotonic() - start > timeout:
                raise TimeoutError(
                    f"Version IDs {version_ids} not ready after {timeout}s: {report}"
                )
            time.sleep(poll_interval)

    def ingest(
        self,
        source: bytes | Path | str | None = None,
        *,
        content: bytes | None = None,
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

        payload_bytes: bytes
        if content is not None:
            payload_bytes = content
            if source is not None and filename is None:
                filename = str(source)
        elif isinstance(source, Path):
            payload_bytes = source.read_bytes()
            filename = filename or source.name
            mime = mime or mimetypes.guess_type(source.name)[0]
        elif isinstance(source, bytes):
            payload_bytes = source
        elif isinstance(source, str):
            p = Path(source)
            if p.is_file():
                payload_bytes = p.read_bytes()
                filename = filename or p.name
                mime = mime or mimetypes.guess_type(p.name)[0]
            else:
                raise ValueError(f"file not found: {source}")
        else:
            raise ValueError("either source or content must be provided to ingest")

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
                content=payload_bytes,
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
    from remember.query_sandbox.errors import SandboxRejection
    from remember.query_sandbox.mcp_tools import validate_saved_query_identifier

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


DEFAULT_BASE_URL = "https://remember.dev/app/api"

#: Environment variables, named so they cannot be confused with the memory
#: client's ``REMEMBERSTACK_*`` pair — a machine often holds both.
TOKEN_ENV = "REMEMBER_CLOUD_TOKEN"
ORG_ENV = "REMEMBER_CLOUD_ORG"
BASE_URL_ENV = "REMEMBER_CLOUD_URL"

#: Environment variables for the unified data-plane client (D65).
API_KEY_ENV = "REMEMBER_API_KEY"
API_URL_ENV = "REMEMBER_API_URL"
REMEMBERSTACK_AUTH_ENV = "REMEMBERSTACK_API_AUTHORIZATION"
REMEMBERSTACK_URL_ENV = "REMEMBERSTACK_API_URL"


class _ClientEnv(BaseSettings):
    """Configuration read from environment variables via pydantic-settings (TID251)."""

    model_config = SettingsConfigDict(extra="ignore")

    remember_api_key: str | None = None
    remember_data_plane_url: str | None = None
    remember_api_url: str | None = None
    rememberstack_api_authorization: str | None = None
    rememberstack_api_url: str | None = None
    remember_cloud_token: str | None = None
    remember_cloud_org: str | None = None
    remember_cloud_url: str | None = None


def _format_bearer(token: str) -> str:
    """Ensure a token string has the standard Bearer header prefix."""
    if not token or not token.strip():
        raise ValueError("API key or authorization token cannot be empty")
    if "\r" in token or "\n" in token:
        raise ValueError("Authorization token must not contain newline characters")
    cleaned = token.strip()
    if cleaned.lower() == "bearer":
        raise ValueError("Bearer token value cannot be empty")
    if cleaned.lower().startswith("bearer "):
        rest = cleaned[7:].strip()
        if not rest:
            raise ValueError("Bearer token value cannot be empty")
        return f"Bearer {rest}"
    return f"Bearer {cleaned}"


class Client(MemoryClient):
    """Ergonomic data-plane memory client for remember.dev (D65).

    Subclasses :class:`rememberstack.client.MemoryClient`, providing:
    - ``api_key`` parameter accepting bare secrets (``umc_dp_...``) or full
      ``Bearer`` headers.
    - Path string support in :meth:`ingest` (accepts ``str``, ``Path``, or ``bytes``).
    - Environment configuration from ``REMEMBER_API_KEY`` and ``REMEMBER_API_URL``
      with fallbacks to ``REMEMBERSTACK_API_AUTHORIZATION`` and ``REMEMBERSTACK_API_URL``.
    - Direct connection to deployment ingress (queries are never proxied through
      the control plane).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        api_url: str | None = None,
        data_plane_url: str | None = None,
        authorization: str | None = None,
        client: httpx.Client | None = None,
        timeout: float | None = None,
        settings: ClientSettings | None = None,
    ) -> None:
        if client is not None and any(
            value is not None
            for value in (
                api_key,
                base_url,
                api_url,
                data_plane_url,
                authorization,
                timeout,
                settings,
            )
        ):
            raise ValueError(
                "an injected client cannot be combined with client settings"
            )
        if client is not None:
            super().__init__(client=client)
            return

        env = _ClientEnv.model_validate({})
        resolved_authorization: str | None = None
        if api_key is not None:
            resolved_authorization = _format_bearer(api_key)
        elif authorization is not None:
            resolved_authorization = authorization
        elif settings is not None and settings.api_authorization is not None:
            resolved_authorization = settings.api_authorization.get_secret_value()
        elif env.remember_api_key:
            resolved_authorization = _format_bearer(env.remember_api_key)
        elif env.rememberstack_api_authorization:
            resolved_authorization = env.rememberstack_api_authorization

        effective_base_url = (
            data_plane_url
            if data_plane_url is not None
            else (base_url if base_url is not None else api_url)
        )
        resolved_base_url: str | None = None
        if effective_base_url is not None:
            resolved_base_url = effective_base_url
        elif settings is not None and settings.api_url:
            resolved_base_url = settings.api_url
        elif env.remember_data_plane_url:
            resolved_base_url = env.remember_data_plane_url
        elif env.remember_api_url:
            resolved_base_url = env.remember_api_url
        elif env.rememberstack_api_url:
            resolved_base_url = env.rememberstack_api_url

        super().__init__(
            base_url=resolved_base_url,
            authorization=resolved_authorization,
            timeout=timeout,
            settings=settings,
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> Self:
        """Build from environment variables with keyword argument overrides."""
        return cls(**overrides)

    def ingest(
        self,
        source: bytes | Path | str | None = None,
        *,
        content: bytes | None = None,
        filename: str | None = None,
        mime: str | None = None,
        title: str | None = None,
        source_kind: str | None = None,
        source_ref: str | None = None,
        source_modified_at: datetime | None = None,
        versioning_mode: Literal["snapshot", "living"] = "snapshot",
        source_version_ref: str | None = None,
    ) -> IngestedVersion:
        """Ingest a document from a file path, string path, or raw bytes."""
        resolved_source = Path(source) if isinstance(source, str) else source
        return super().ingest(
            resolved_source,
            content=content,
            filename=filename,
            mime=mime,
            title=title,
            source_kind=source_kind,
            source_ref=source_ref,
            source_modified_at=source_modified_at,
            versioning_mode=versioning_mode,
            source_version_ref=source_version_ref,
        )

    def ingest_file(
        self,
        file_path: str | Path,
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
        """Alias for :meth:`ingest` accepting a string file path or :class:`pathlib.Path`."""
        return self.ingest(
            file_path,
            filename=filename,
            mime=mime,
            title=title,
            source_kind=source_kind,
            source_ref=source_ref,
            source_modified_at=source_modified_at,
            versioning_mode=versioning_mode,
            source_version_ref=source_version_ref,
        )

    def __enter__(self) -> Self:
        """Support ``with Client(...) as memory:`` returning Self."""
        super().__enter__()
        return self


class CloudClient:
    """Ask the control plane what it knows about one organisation.

    The credential is organisation-bound, so the organisation is fixed for the
    life of the client rather than passed per call: a control token cannot act
    on another organisation, and an API that invited you to try would be
    misleading.
    """

    def __init__(
        self,
        *,
        token: str,
        org_id: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind a credential to one organisation."""
        if not token:
            raise ValueError("a control-plane token is required")
        if not org_id:
            raise ValueError("an organisation id is required")
        self._org_id = org_id
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> Self:
        """Build from ``REMEMBER_CLOUD_TOKEN`` / ``_ORG`` / ``_URL``.

        The usual shape for an agent: credentials in the environment, nothing in
        the code.
        """
        env = _ClientEnv.model_validate({})
        token = overrides.pop("token", None) or env.remember_cloud_token or ""
        org_id = overrides.pop("org_id", None) or env.remember_cloud_org or ""
        base_url = (
            overrides.pop("base_url", None)
            or env.remember_cloud_url
            or DEFAULT_BASE_URL
        )
        if not token:
            raise ValueError(
                f"set {TOKEN_ENV} to a control-plane token (umc_cp_…). "
                "Mint one with POST /v1/orgs/<org>/control-tokens while signed "
                "in; a deployment token (umc_dp_…) is a different credential "
                "and the control plane rejects it"
            )
        if not org_id:
            raise ValueError(f"set {ORG_ENV} to your organisation id")
        return cls(token=token, org_id=org_id, base_url=base_url, **overrides)

    @property
    def org_id(self) -> str:
        """The organisation this credential is bound to."""
        return self._org_id

    # -- the questions -------------------------------------------------

    def billing_status(self) -> BillingStatus:
        """Whether chargeable work may run, and what the balance is."""
        return BillingStatus.from_payload(
            self._get(f"/v1/orgs/{self._org_id}/billing/status")
        )

    def deployments(self) -> list[Deployment]:
        """Every deployment this organisation has (today, zero or one)."""
        payload = self._get(f"/v1/orgs/{self._org_id}/deployments")
        rows = payload if isinstance(payload, list) else payload.get("items", [])
        return [Deployment.from_payload(row) for row in rows]

    def deployment(self) -> Deployment | None:
        """The organisation's deployment, or None before one is provisioned."""
        found = self.deployments()
        return found[0] if found else None

    def ledger(self, *, limit: int = 50) -> list[LedgerEntry]:
        """The credit ledger: what was charged, newest first as the server sends.

        ``limit`` is bounded by the server to 1..200; values outside that range
        are rejected there rather than silently clamped here, so a caller sees
        its own mistake.
        """
        payload = self._get(
            f"/v1/orgs/{self._org_id}/billing/ledger", params={"limit": limit}
        )
        rows = payload if isinstance(payload, list) else payload.get("items", [])
        return [LedgerEntry.from_payload(row) for row in rows]

    def spend_gate(self, *, deployment_id: str) -> SpendGate:
        """May work dispatch right now — and if not, why.

        Worth asking before a large ingest: a refusal here is cheaper than a
        refusal halfway through one.
        """
        return SpendGate.from_payload(
            self._get(
                f"/v1/orgs/{self._org_id}/deployments/{deployment_id}/spend-safety/gate"
            )
        )

    def is_ready(self) -> bool:
        """One call an agent can branch on: is there a deployment able to serve.

        Convenience over :meth:`deployment`, because "am I ready" is the
        question actually being asked.
        """
        found = self.deployment()
        return found is not None and found.is_ready

    # -- plumbing ------------------------------------------------------

    def _get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """Perform a read, translating D41 error envelopes into exceptions."""
        try:
            response = self._http.get(path, params=params)
        except httpx.TimeoutException as error:
            raise CloudError(f"timed out calling {path}", retryable=True) from error
        except httpx.HTTPError as error:
            raise CloudError(f"could not reach {path}: {error}") from error

        if response.is_success:
            return response.json()
        raise _as_error(response)

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._http.close()

    def __enter__(self) -> Self:
        """Support ``with CloudClient(...) as cloud:``."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on exit."""
        self.close()


def _as_error(response: httpx.Response) -> CloudError:
    """Turn a non-success response into the narrowest exception that fits.

    D41's envelope is ``{"detail": {code, message, retryable, request_id}}``. A
    response that does not carry it — a proxy error page, say — still produces a
    typed exception, so a caller never has to handle two failure shapes.
    """
    code: str | None = None
    message = f"HTTP {response.status_code}"
    retryable = False
    request_id = response.headers.get("X-Request-Id")

    with _tolerating_bad_json():
        body = response.json()
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            code = detail.get("code")
            message = detail.get("message") or message
            retryable = bool(detail.get("retryable", False))
            request_id = detail.get("request_id") or request_id
        elif isinstance(detail, str):
            # Pre-D41 routes still answer with a bare string.
            message = detail

    shared = {
        "status_code": response.status_code,
        "code": code,
        "retryable": retryable,
        "request_id": request_id,
    }
    if response.status_code == 401:
        return Unauthenticated(message, **shared)  # type: ignore[arg-type]
    if response.status_code == 403:
        return NotPermitted(message, **shared)  # type: ignore[arg-type]
    if response.status_code == 429:
        return RateLimited(
            message,
            retry_after=_retry_after(response),
            **shared,  # type: ignore[arg-type]
        )
    return CloudError(message, **shared)  # type: ignore[arg-type]


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds from ``Retry-After``, when the server sent a usable one."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        # HTTP-date form; the caller's own backoff is better than a bad guess.
        return None


@contextmanager
def _tolerating_bad_json() -> Iterator[None]:
    """Ignore an unparseable error body rather than masking the real failure."""
    try:
        yield
    except (ValueError, AttributeError):
        return
