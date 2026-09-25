"""The remember memory clients (D62/D65/D136).

- :class:`MemoryClient`: the typed synchronous client for one engine's HTTP API.
- :class:`Client`: the same, plus file-path ingest and ``client.account``.

Both resolve their connection with :func:`remember.connection.resolve_connection`
— explicit arguments, then ``REMEMBER_*`` environment variables, then the
stored credential file — exactly as the CLI does.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import time
from typing import Final
from typing import Literal
from typing import Self
from typing import TypeVar
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from remember.connection import Connection
from remember.connection import EngineRoute
from remember.connection import resolve_connection
from remember.errors import AccountApiUnavailable
from remember.errors import MemoryApiError
from remember.errors import PipelineDeadLettered
from remember.errors import RateLimited
from remember.issuer import fetch_issuer_metadata
from remember.issuer import IssuerError
from remember.issuer import send_same_origin
from remember.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools import validate_arguments
from remember.mcp_tools import validate_saved_query_identifier
from remember.mime import infer_upload_mime
from remember.models import ADJACENT_CHUNKS_MAX_WINDOW
from remember.models import ADJACENT_CHUNKS_MIN_WINDOW
from remember.models import ConnectorCreate
from remember.models import ConnectorDescriptor
from remember.models import ContextBundleV2
from remember.models import DeploymentBuildInfo
from remember.models import DocumentDeletion
from remember.models import DocumentPage
from remember.models import DocumentStatusFilter
from remember.models import Envelope
from remember.models import IngestedVersion
from remember.models import PipelineReadinessReport
from remember.models import QueryResultDict
from remember.models import ReadinessRequirements
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
        api_key: str | None = None,
        base_url: str | None = None,
        project: str | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Resolve the connection, or wrap an injected ``httpx.Client`` as is.

        ``api_key``, ``base_url`` and ``project`` take precedence over
        ``REMEMBER_API_KEY``, ``REMEMBER_API_URL`` and ``REMEMBER_PROJECT``,
        which take precedence over the stored credential file. Construction
        makes no network call; a signed key's deployment is resolved on first
        use. ``transport`` replaces the network (tests, proxies).

        An injected ``client`` is used unchanged — its base URL and headers are
        the caller's — and cannot be combined with the other settings.
        """
        if client is not None:
            if any(
                value is not None for value in (api_key, base_url, project, transport)
            ):
                raise ValueError(
                    "an injected client cannot be combined with client settings"
                )
            self._owned = False
            self._http = client
            self._connection: Connection | None = None
            self._route: EngineRoute | None = None
            return
        self._owned = True
        self._http = httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=False
        )
        self._connection = resolve_connection(
            api_key=api_key, api_url=base_url, project=project
        )
        self._route = EngineRoute(connection=self._connection, http=self._http)

    def __enter__(self) -> "MemoryClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close only a transport the SDK created itself."""
        if self._owned:
            self._http.close()

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
    ) -> Envelope | ContextBundleV2:
        """Run one assured operation and validate its exact wire contract."""
        path = f"/operations/{quote(name, safe='')}"
        endpoint = f"POST {path}"
        payload = self._json("POST", path, json_body=dict(arguments or {}))
        if isinstance(payload, dict) and payload.get("contract") == "ContextBundle/v2":
            return _validated(ContextBundleV2, payload, endpoint=endpoint)
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

    def facts_context(
        self,
        query: str,
        *,
        time: Mapping[str, object] | None = None,
        hops: int | None = None,
        predicate: str | None = None,
        entity_ids: Sequence[str | UUID] | None = None,
    ) -> Envelope:
        """Run the assured facts_context operation."""
        args: dict[str, object] = {"query": query}
        if time is not None:
            args["time"] = dict(time)
        if hops is not None:
            args["hops"] = hops
        if predicate is not None:
            args["predicate"] = predicate
        if entity_ids is not None:
            args["entity_ids"] = [str(e) for e in entity_ids]
        res = self.run_operation(name="facts_context", arguments=args)
        assert isinstance(res, Envelope)
        return res

    def combined_context(
        self, query: str, *, time: Mapping[str, object] | None = None
    ) -> ContextBundleV2:
        """Run the assured combined_context operation."""
        args: dict[str, object] = {"query": query}
        if time is not None:
            args["time"] = dict(time)
        res = self.run_operation(name="combined_context", arguments=args)
        assert isinstance(res, ContextBundleV2)
        return res

    def claims_and_sources_context(self, query: str) -> Envelope:
        """Run the assured claims_and_sources_context operation."""
        res = self.run_operation(
            name="claims_and_sources_context", arguments={"query": query}
        )
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
        if name not in OPEN_QUERY_TOOL_NAMES:
            raise ValueError(f"unknown open-query tool {name!r}")
        args = validate_arguments(name, arguments)
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
        k: int = 50,
    ) -> Envelope:
        """Read current or valid-time relations matching an optional pattern."""
        params: dict[str, str | int] = {"k": k}
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
            self._json("GET", "/lookup/relations", params=params),
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

    def adjacent_chunks(self, *, chunk_id: UUID | str, window: int = 1) -> Envelope:
        """Fetch surrounding source chunks within a window around a target chunk in document order."""
        chunk_uuid = UUID(str(chunk_id))
        if window < ADJACENT_CHUNKS_MIN_WINDOW or window > ADJACENT_CHUNKS_MAX_WINDOW:
            raise ValueError(
                f"window must be between {ADJACENT_CHUNKS_MIN_WINDOW} and {ADJACENT_CHUNKS_MAX_WINDOW}"
            )
        return _validated(
            Envelope,
            self._json(
                "GET", f"/chunks/{chunk_uuid}/adjacent", params={"window": window}
            ),
            endpoint=f"GET /chunks/{chunk_uuid}/adjacent",
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
        timeout: float = 1800.0,
        poll_interval: float = 15.0,
        require_p3: bool = False,
    ) -> PipelineReadinessReport:
        """Poll /readiness until every listed version is ready.

        The first check is immediate, so a version that is already processed
        (for example one whose ingest returned ``created=False``) returns at
        once. Later checks are ``poll_interval`` seconds apart.

        The defaults — ``timeout`` 30 minutes, ``poll_interval`` 15 seconds —
        are starting points sized for single documents, where processing takes
        minutes; raise ``timeout`` for bulk loads.

        A stage whose status is ``failed`` has a retry scheduled and can still
        succeed, so waiting continues. A stage that is ``dead_letter`` has used
        all its retries and never becomes ready, so the wait stops at once with
        :class:`~remember.errors.PipelineDeadLettered`. ``TimeoutError`` is
        raised when ``timeout`` seconds pass first.
        """
        deadline = time.monotonic() + timeout
        req_ids = tuple(UUID(str(v)) for v in version_ids)
        require = ReadinessRequirements(
            pipeline=True, p1=True, live_graph=True, p3=require_p3
        )
        while True:
            report = self.pipeline_readiness(version_ids=req_ids, require=require)
            if report.ready:
                return report
            dead_lettered = tuple(
                (version.version_id, stage.stage, stage.status)
                for version in report.versions
                for stage in version.stages
                if stage.status == "dead_letter"
            )
            if dead_lettered:
                raise PipelineDeadLettered(dead_lettered=dead_lettered, report=report)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Version IDs {list(version_ids)} not ready after {timeout}s:"
                    f" {report}"
                )
            time.sleep(min(poll_interval, remaining))

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

        # An explicit mime always wins. Otherwise a file path's type comes
        # from the real path name (an overridden filename does not change
        # it), and bytes take the type of the filename they are sent under.
        payload_bytes: bytes
        if isinstance(source, str):
            source = Path(source)  # a missing path raises FileNotFoundError
        if content is not None:
            payload_bytes = content
            if source is not None and filename is None:
                filename = str(source)
        elif isinstance(source, Path):
            payload_bytes = source.read_bytes()
            filename = filename or source.name
            mime = mime or infer_upload_mime(source.name)
        elif isinstance(source, bytes):
            payload_bytes = source
        else:
            raise ValueError("either source or content must be provided to ingest")

        if not filename:
            raise ValueError("filename is required when ingesting bytes")
        mime = mime or infer_upload_mime(filename) or "application/octet-stream"
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

    def list_documents(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: DocumentStatusFilter | None = None,
    ) -> DocumentPage:
        """One page of the deployment's documents, newest lineage first.

        Pass the returned ``cursor`` back to read the next page; ``None``
        means there are no more. ``status`` filters on each document's newest
        version, for example ``"failed"``.
        """
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if status is not None:
            params["status"] = status
        return _validated(
            DocumentPage,
            self._json("GET", "/documents", params=params),
            endpoint="GET /documents",
        )

    def delete_document(self, *, doc_id: UUID | str) -> DocumentDeletion:
        """Remove one document from the live memory.

        Its claims stop being current testimony and facts that no other
        document supports are closed. The claims and the stored original stay
        as history. An unknown or already deleted ``doc_id`` raises
        ``MemoryApiError`` with ``status_code`` 404.
        """
        document = UUID(str(doc_id))
        return _validated(
            DocumentDeletion,
            self._json("DELETE", f"/documents/{document}"),
            endpoint="DELETE /documents/{doc_id}",
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

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | tuple[tuple[str, str], ...] | None = None,
        json: object | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send one engine request, re-resolving a moved deployment once.

        Only a key-routed client re-resolves (D136 §8.3), and retries once when
        the deployment URL changed. A read (``GET``/``HEAD``) retries after any
        network failure, a ``421``, or a ``404`` that is not the engine's error
        envelope. Any other request retries only after a failure that happens
        before the request reaches the server — a connect error, a connect
        timeout, or a ``421`` — so a write is never sent twice.
        """
        merged: dict[str, str] = dict(headers or {})
        if self._route is None:
            try:
                return self._http.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    content=content,
                    headers=merged,
                )
            except httpx.HTTPError as error:
                raise MemoryApiError(status_code=0, detail=str(error)) from error
        read = method in ("GET", "HEAD")
        for attempt in (1, 2):
            base, authorization = self._route.target()
            if authorization is not None:
                merged["Authorization"] = authorization
            url = base.rstrip("/") + path
            try:
                response = self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    content=content,
                    headers=merged,
                )
            except (httpx.NetworkError, httpx.ConnectTimeout) as error:
                unsent = isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout))
                if attempt == 1 and (read or unsent) and self._route.re_resolve():
                    continue
                raise MemoryApiError(status_code=0, detail=str(error)) from error
            except httpx.HTTPError as error:
                raise MemoryApiError(status_code=0, detail=str(error)) from error
            if (
                attempt == 1
                and (response.status_code == 421 or (read and _looks_moved(response)))
                and self._route.key_routed
                and self._route.re_resolve()
            ):
                response.close()
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

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
        response = self._send(
            method,
            path,
            params=params,
            json=json_body,
            content=content,
            headers=headers,
        )
        if response.status_code == 429:
            raise _rate_limited(response)
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


class Client(MemoryClient):
    """The memory client, plus file-path ingest and the issuer's account API.

    ``Client(api_key="rmb_…")`` reaches the key's default project without the
    caller naming a host; ``Client(api_key="rmb_…", project="docs")`` another
    project the key covers; ``Client()`` a self-hosted engine at
    ``REMEMBER_API_URL`` or ``http://127.0.0.1:8000``. See
    :class:`MemoryClient` for the arguments.
    """

    @classmethod
    def from_env(cls, **overrides: object) -> Self:
        """Same as ``Client(**overrides)``: arguments, then environment, then the file."""
        return cls(**overrides)  # type: ignore[arg-type]

    @property
    def account(self) -> AccountApi:
        """The key issuer's account API, called with the same key.

        Raises :class:`~remember.errors.AccountApiUnavailable` on use when the
        key has no issuer or the issuer advertises no account API.
        """
        return AccountApi(connection=self._connection, http=self._http)

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


class AccountApi:
    """Calls to the account API of the key's issuer (D136 §8.3).

    The issuer is the signed key's ``iss``; the API's base URL is the issuer
    metadata's ``remember_account_endpoint``. Which operations exist, and
    which permissions they need, is the issuer's to define.
    """

    def __init__(self, *, connection: Connection | None, http: httpx.Client) -> None:
        """Bind the client's resolved connection and HTTP client."""
        self._connection = connection
        self._http = http

    def whoami(self) -> dict[str, object]:
        """The issuer's view of this key: person, organisation, projects, permissions."""
        payload = self.get("/v1/keys/self")
        if not isinstance(payload, dict):
            raise MemoryApiError(
                status_code=200, detail="GET /v1/keys/self did not return an object"
            )
        return payload

    def get(
        self, path: str, *, params: Mapping[str, str | int] | None = None
    ) -> object:
        """``GET`` one account-API path (relative to the account endpoint)."""
        base = self._base_url()
        assert self._connection is not None and self._connection.authorization
        request = self._http.build_request(
            "GET",
            base.rstrip("/") + "/" + path.lstrip("/"),
            params=params,
            headers={
                "Authorization": self._connection.authorization,
                "Accept": "application/json",
            },
        )
        try:
            response = send_same_origin(self._http, request)
        except httpx.HTTPError as error:
            raise MemoryApiError(status_code=0, detail=str(error)) from error
        if response.status_code == 429:
            raise _rate_limited(response)
        if not response.is_success:
            raise MemoryApiError(
                status_code=response.status_code, detail=_error_detail(response)
            )
        try:
            return response.json()
        except ValueError as error:
            raise MemoryApiError(
                status_code=response.status_code,
                detail=f"GET {path} returned invalid JSON",
            ) from error

    def _base_url(self) -> str:
        connection = self._connection
        if connection is None or connection.claims is None:
            raise AccountApiUnavailable(
                detail=(
                    "the account API needs a signed key from an issuer; this "
                    "client has none (a self-hosted engine has no account API)"
                )
            )
        metadata = fetch_issuer_metadata(connection.claims.iss, http=self._http)
        if not metadata.remember_account_endpoint:
            raise AccountApiUnavailable(
                detail=f"issuer {metadata.issuer} advertises no remember_account_endpoint"
            )
        try:
            return metadata.endpoint("remember_account_endpoint")
        except IssuerError as error:
            raise AccountApiUnavailable(detail=error.detail) from error


def _looks_moved(response: httpx.Response) -> bool:
    """``421``, or a ``404`` whose body is not the engine's error envelope."""
    if response.status_code == 421:
        return True
    if response.status_code != 404:
        return False
    try:
        body = response.json()
    except ValueError:
        return True
    return not (isinstance(body, dict) and "detail" in body)


def _rate_limited(response: httpx.Response) -> RateLimited:
    """The typed ``429``: admission code, message, and ``Retry-After``."""
    code: str | None = None
    detail = "rate limited"
    try:
        body = response.json()
    except ValueError:
        body = None
    envelope = body.get("detail") if isinstance(body, dict) else None
    if isinstance(envelope, dict):
        if isinstance(envelope.get("code"), str):
            code = envelope["code"]
        detail = str(envelope.get("message") or code or detail)
    elif isinstance(envelope, str):
        detail = envelope
    return RateLimited(detail=detail, code=code, retry_after=_retry_after(response))


def _error_detail(response: httpx.Response) -> str:
    """The message of an error response, whatever envelope it came in."""
    try:
        body = response.json()
    except ValueError:
        return response.text
    envelope = body.get("detail") if isinstance(body, dict) else None
    if isinstance(envelope, dict):
        return str(envelope.get("message") or envelope.get("code") or envelope)
    if envelope is not None:
        return str(envelope)
    return response.text


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
