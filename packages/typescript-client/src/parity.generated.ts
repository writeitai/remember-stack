/* Generated from Python; regenerate with npm run generate. */
export default {
  "QueryResultDict": [
    "rows",
    "columns",
    "truncated"
  ],
  "adaptations": "typescript_client_design.md#7-language-and-safety-adaptations",
  "classes": {
    "AccountApi": {
      "__init__": {
        "arguments": "self, *, connection: Connection | None, http: httpx.Client",
        "property": false,
        "returns": "None"
      },
      "get": {
        "arguments": "self, path: str, *, params: Mapping[str, str | int] | None=None",
        "property": false,
        "returns": "object"
      },
      "whoami": {
        "arguments": "self",
        "property": false,
        "returns": "dict[str, object]"
      }
    },
    "Client": {
      "account": {
        "arguments": "self",
        "property": true,
        "returns": "AccountApi"
      },
      "from_env": {
        "arguments": "cls, **overrides: object",
        "property": false,
        "returns": "Self"
      },
      "ingest": {
        "arguments": "self, source: bytes | Path | str | None=None, *, content: bytes | None=None, filename: str | None=None, mime: str | None=None, title: str | None=None, source_kind: str | None=None, source_ref: str | None=None, source_modified_at: datetime | None=None, versioning_mode: Literal['snapshot', 'living']='snapshot', source_version_ref: str | None=None, source_path: str | None=None, version_key: str | None=None, effective_from: datetime | None=None, effective_until: datetime | None=None",
        "property": false,
        "returns": "IngestedVersion"
      },
      "ingest_file": {
        "arguments": "self, file_path: str | Path, *, filename: str | None=None, mime: str | None=None, title: str | None=None, source_kind: str | None=None, source_ref: str | None=None, source_modified_at: datetime | None=None, versioning_mode: Literal['snapshot', 'living']='snapshot', source_version_ref: str | None=None, source_path: str | None=None, version_key: str | None=None, effective_from: datetime | None=None, effective_until: datetime | None=None",
        "property": false,
        "returns": "IngestedVersion"
      }
    },
    "MemoryClient": {
      "__init__": {
        "arguments": "self, *, api_key: str | None=None, base_url: str | None=None, project: str | None=None, timeout: float=30.0, client: httpx.Client | None=None, transport: httpx.BaseTransport | None=None",
        "property": false,
        "returns": "None",
        "typescriptOptions": "{apiKey?, baseUrl?, project?, timeoutMs?, client?: HttpClient, transport?: HttpTransport, agents?: {http?: NodeHttpAgent, https?: NodeHttpsAgent}}"
      },
      "add_connector": {
        "arguments": "self, *, connector: ConnectorCreate",
        "property": false,
        "returns": "ConnectorDescriptor"
      },
      "adjacent_chunks": {
        "arguments": "self, *, chunk_id: UUID | str, window: int=1",
        "property": false,
        "returns": "Envelope"
      },
      "call_open_query": {
        "arguments": "self, *, name: str, arguments: Mapping[str, object]",
        "property": false,
        "returns": "object"
      },
      "claims_and_sources_context": {
        "arguments": "self, query: str, *, time: Mapping[str, object] | None=None",
        "property": false,
        "returns": "Envelope"
      },
      "clear_effective_time": {
        "arguments": "self, *, doc_id: UUID | str",
        "property": false,
        "returns": "EffectiveTimeCleared"
      },
      "close": {
        "arguments": "self",
        "property": false,
        "returns": "None"
      },
      "combined_context": {
        "arguments": "self, query: str, *, time: Mapping[str, object] | None=None",
        "property": false,
        "returns": "ContextBundleV2"
      },
      "connector_status": {
        "arguments": "self, *, connector_id: UUID",
        "property": false,
        "returns": "ConnectorDescriptor"
      },
      "connectors": {
        "arguments": "self",
        "property": false,
        "returns": "tuple[ConnectorDescriptor, ...]"
      },
      "delete_document": {
        "arguments": "self, *, doc_id: UUID | str",
        "property": false,
        "returns": "DocumentDeletion"
      },
      "deployment_build_info": {
        "arguments": "self",
        "property": false,
        "returns": "DeploymentBuildInfo"
      },
      "describe_query_space": {
        "arguments": "self, *, pattern: str | None=None, include_examples: bool=False",
        "property": false,
        "returns": "dict[str, object]"
      },
      "describe_saved_query": {
        "arguments": "self, *, namespace: str, name: str, version: int | None=None",
        "property": false,
        "returns": "dict[str, object]"
      },
      "document_references": {
        "arguments": "self, *, chunk_id: UUID | str | None=None, doc_id: UUID | str | None=None, section_key: str | None=None, direction: Literal['outgoing', 'incoming', 'both']='both', kinds: Sequence[ReferenceKind] | None=None, time: ReadTime | None=None, k: int=DOCUMENT_REFERENCES_DEFAULT_K, cursor: str | None=None",
        "property": false,
        "returns": "DocumentReferencesPage"
      },
      "document_references_request": {
        "arguments": "self, *, request: DocumentReferencesRequest",
        "property": false,
        "returns": "DocumentReferencesPage"
      },
      "explain_query": {
        "arguments": "self, sql: str, *, parameters: Sequence[object]=()",
        "property": false,
        "returns": "QueryResultDict"
      },
      "explain_sql": {
        "arguments": "self, *, sql: str, parameters: list[object] | tuple[object, ...]=()",
        "property": false,
        "returns": "dict[str, object]"
      },
      "facts_context": {
        "arguments": "self, query: str, *, time: Mapping[str, object] | None=None, hops: int | None=None, predicate: str | None=None, entity_ids: Sequence[str | UUID] | None=None",
        "property": false,
        "returns": "Envelope"
      },
      "graph_citation_path": {
        "arguments": "self, *, from_doc_id: UUID, to_doc_id: UUID, max_hops: int=6",
        "property": false,
        "returns": "Envelope"
      },
      "graph_neighborhood": {
        "arguments": "self, *, entity_id: UUID, hops: int=2, predicates: tuple[str, ...]=(), valid_at: datetime | None=None, believed_at: datetime | None=None, limit: int=500, continuation: str | None=None, include_paths: bool=False",
        "property": false,
        "returns": "Envelope"
      },
      "graph_path": {
        "arguments": "self, *, from_entity_id: UUID, to_entity_id: UUID, max_hops: int=4, predicates: tuple[str, ...]=(), valid_at: datetime | None=None, believed_at: datetime | None=None",
        "property": false,
        "returns": "Envelope"
      },
      "hydrate_relation": {
        "arguments": "self, *, relation_id: UUID",
        "property": false,
        "returns": "Envelope"
      },
      "ingest": {
        "arguments": "self, source: bytes | Path | str | None=None, *, content: bytes | None=None, filename: str | None=None, mime: str | None=None, title: str | None=None, source_kind: str | None=None, source_ref: str | None=None, source_modified_at: datetime | None=None, versioning_mode: Literal['snapshot', 'living']='snapshot', source_version_ref: str | None=None, source_path: str | None=None, version_key: str | None=None, effective_from: datetime | None=None, effective_until: datetime | None=None",
        "property": false,
        "returns": "IngestedVersion"
      },
      "list_documents": {
        "arguments": "self, *, limit: int=50, cursor: str | None=None, status: DocumentStatusFilter | None=None",
        "property": false,
        "returns": "DocumentPage"
      },
      "list_operations": {
        "arguments": "self",
        "property": false,
        "returns": "tuple[ToolDescriptor, ...]"
      },
      "list_saved_queries": {
        "arguments": "self, *, namespace: str | None=None, status: str | None=None",
        "property": false,
        "returns": "list[dict[str, object]]"
      },
      "lookup_observations": {
        "arguments": "self, *, entity_id: UUID, property_query: str | None=None, k: int=10",
        "property": false,
        "returns": "Envelope"
      },
      "lookup_relations": {
        "arguments": "self, *, subject_entity_id: UUID | None=None, predicate: str | None=None, object_entity_id: UUID | None=None, valid_at: datetime | None=None, k: int=50",
        "property": false,
        "returns": "Envelope"
      },
      "open_query": {
        "arguments": "self, sql: str, *, parameters: Sequence[object]=(), max_rows: int | None=None",
        "property": false,
        "returns": "QueryResultDict"
      },
      "pause_connector": {
        "arguments": "self, *, connector_id: UUID",
        "property": false,
        "returns": "ConnectorDescriptor"
      },
      "pipeline_readiness": {
        "arguments": "self, *, version_ids: tuple[UUID, ...], require: ReadinessRequirements",
        "property": false,
        "returns": "PipelineReadinessReport"
      },
      "query_sql": {
        "arguments": "self, *, sql: str, parameters: list[object] | tuple[object, ...]=(), max_rows: int | None=None",
        "property": false,
        "returns": "dict[str, object]"
      },
      "reference_generations": {
        "arguments": "self, *, doc_id: UUID | str, version_id: UUID | str",
        "property": false,
        "returns": "ReferenceGenerations"
      },
      "resolve": {
        "arguments": "self, *, name: str, context_entity_ids: tuple[UUID, ...]=()",
        "property": false,
        "returns": "Envelope"
      },
      "resolve_entity": {
        "arguments": "self, name: str",
        "property": false,
        "returns": "Envelope"
      },
      "run_operation": {
        "arguments": "self, *, name: str, arguments: Mapping[str, object] | None=None",
        "property": false,
        "returns": "Envelope | ContextBundleV2"
      },
      "run_saved_query": {
        "arguments": "self, *, namespace: str, name: str, parameters: list[object] | tuple[object, ...]=(), version: int | None=None, max_rows: int | None=None",
        "property": false,
        "returns": "dict[str, object]"
      },
      "search_chunks": {
        "arguments": "self, *, query: str, k: int=10, channel: Literal['semantic', 'bm25']='semantic', documents: DocumentSearchFilters | None=None, time: ReadTime | None=None",
        "property": false,
        "returns": "Envelope"
      },
      "search_claims": {
        "arguments": "self, *, query: str, k: int=10, channel: Literal['semantic', 'bm25']='semantic', documents: DocumentSearchFilters | None=None, time: ReadTime | None=None",
        "property": false,
        "returns": "Envelope"
      },
      "search_documents": {
        "arguments": "self, query: str | None=None, *, filters: DocumentSearchFilters | None=None, versions: Literal['current', 'all']='current', k: int=20, cursor: str | None=None, time: ReadTime | None=None",
        "property": false,
        "returns": "DocumentSearchPage"
      },
      "search_documents_request": {
        "arguments": "self, *, request: DocumentSearchRequest",
        "property": false,
        "returns": "DocumentSearchPage"
      },
      "search_query_space": {
        "arguments": "self, *, query: str, k: int=10",
        "property": false,
        "returns": "list[dict[str, object]]"
      },
      "section_history": {
        "arguments": "self, *, doc_id: UUID | str, section_key: str, time: ReadTime | None=None, k: int=SECTION_HISTORY_DEFAULT_K, cursor: str | None=None",
        "property": false,
        "returns": "SectionHistoryPage"
      },
      "section_history_request": {
        "arguments": "self, *, request: SectionHistoryRequest",
        "property": false,
        "returns": "SectionHistoryPage"
      },
      "set_effective_periods": {
        "arguments": "self, *, doc_id: UUID | str, version_id: UUID | str, periods: Sequence[EffectivePeriodInput]",
        "property": false,
        "returns": "EffectivePeriodsSet"
      },
      "set_references": {
        "arguments": "self, *, doc_id: UUID | str, version_id: UUID | str, references: Sequence[ReferenceInput]",
        "property": false,
        "returns": "ReferencesSet"
      },
      "transcript_relation": {
        "arguments": "self, *, relation_id: UUID",
        "property": false,
        "returns": "Envelope"
      },
      "wait_for_readiness": {
        "arguments": "self, version_ids: Sequence[str | UUID], *, timeout: float=1800.0, poll_interval: float=15.0, require_p3: bool=False",
        "property": false,
        "returns": "PipelineReadinessReport"
      }
    }
  },
  "exports": [
    "AccountApi",
    "AccountApiUnavailable",
    "CapabilityReadiness",
    "ClaimOccurrence",
    "ClaimValidPrecision",
    "Client",
    "ConnectorCreate",
    "ConnectorDescriptor",
    "ConnectorNotFoundError",
    "ContextBundleV2",
    "DeclaredEffectivePeriod",
    "DocumentDeletion",
    "DocumentPage",
    "DocumentReference",
    "DocumentReferenceSource",
    "DocumentReferenceTarget",
    "DocumentReferencesPage",
    "DocumentReferencesRequest",
    "DocumentReferencesTooBroad",
    "DocumentSearchFilters",
    "DocumentSearchPage",
    "DocumentSearchRequest",
    "DocumentSearchResult",
    "DocumentSummary",
    "DocumentVersionSummary",
    "EffectiveInterval",
    "EffectivePeriodInput",
    "EffectivePeriodsSet",
    "EffectiveTimeCleared",
    "Envelope",
    "IngestedVersion",
    "MatchingEdition",
    "MemoryApiError",
    "MemoryClient",
    "NamedReferenceTarget",
    "PipelineDeadLettered",
    "PipelineReadinessReport",
    "PipelineStageReadiness",
    "ProjectResolutionError",
    "QueryResultDict",
    "RateLimited",
    "ReadTime",
    "ReadinessRequirements",
    "ReferenceGeneration",
    "ReferenceGenerations",
    "ReferenceInput",
    "ReferenceItemError",
    "ReferenceTarget",
    "ReferenceWindow",
    "ReferencesSet",
    "RememberClient",
    "ScopePending",
    "SectionAmendment",
    "SectionHistoryPage",
    "SectionHistoryRequest",
    "SectionHistoryRow",
    "SectionHistorySection",
    "StoredKeyRefused",
    "TemporalMatch",
    "ToolDescriptor",
    "VersionPipelineReadiness",
    "__version__",
    "resolve_connection"
  ],
  "publishedBaseline": {
    "revision": "dd0c78015099cdd84ab5f3501744d6e225125989",
    "version": "0.17.2"
  },
  "pythonSourceRevision": "2cde3baf7a820164170f2e4b91065056309f68e8",
  "resolve_connection": {
    "arguments": "*, api_key: str | None=None, api_url: str | None=None, project: str | None=None, issuer: str | None=None, mcp_url: str | None=None",
    "returns": "Connection",
    "typescript": "resolveConnection"
  },
  "supportExports": {
    "functions": {
      "remember.connection.clear_host_cache": {
        "arguments": "",
        "returns": "None",
        "typescript": "clearHostCache"
      },
      "remember.connection.environment_issuer": {
        "arguments": "",
        "returns": "str | None",
        "typescript": "environmentIssuer"
      },
      "remember.connection.normalize_key": {
        "arguments": "value: str",
        "returns": "str",
        "typescript": "normalizeKey"
      },
      "remember.connection.resolve_project": {
        "arguments": "*, key: str, claims: KeyClaims, project: str | None, http: httpx.Client, clock: Callable[[], float]=time.monotonic, refresh: bool=False",
        "returns": "ResolvedProject",
        "typescript": "resolveProject"
      },
      "remember.credentials.config_dir": {
        "arguments": "",
        "returns": "Path",
        "typescript": "configDir"
      },
      "remember.credentials.credentials_path": {
        "arguments": "",
        "returns": "Path",
        "typescript": "credentialsPath"
      },
      "remember.credentials.load_credentials": {
        "arguments": "",
        "returns": "StoredCredentials | None",
        "typescript": "loadCredentials"
      },
      "remember.issuer.clear_metadata_cache": {
        "arguments": "",
        "returns": "None",
        "typescript": "clearMetadataCache"
      },
      "remember.issuer.fetch_issuer_metadata": {
        "arguments": "issuer: str, *, http: httpx.Client",
        "returns": "IssuerMetadata",
        "typescript": "fetchIssuerMetadata"
      },
      "remember.issuer.is_loopback": {
        "arguments": "host: str",
        "returns": "bool",
        "typescript": "isLoopback"
      },
      "remember.issuer.metadata_url": {
        "arguments": "issuer: str",
        "returns": "str",
        "typescript": "metadataUrl"
      },
      "remember.issuer.normalize_issuer": {
        "arguments": "issuer: str",
        "returns": "str",
        "typescript": "normalizeIssuer"
      },
      "remember.issuer.origin": {
        "arguments": "url: str | httpx.URL",
        "returns": "tuple[str, str, int | None]",
        "typescript": "origin"
      },
      "remember.issuer.require_secure_url": {
        "arguments": "url: str, *, what: str",
        "returns": "httpx.URL",
        "typescript": "requireSecureUrl"
      },
      "remember.issuer.same_origin": {
        "arguments": "left: str | httpx.URL, right: str | httpx.URL",
        "returns": "bool",
        "typescript": "sameOrigin"
      },
      "remember.issuer.send_same_origin": {
        "arguments": "http: httpx.Client, request: httpx.Request, *, max_redirects: int=_MAX_REDIRECTS, stream: bool=False",
        "returns": "httpx.Response",
        "typescript": "sendSameOrigin"
      },
      "remember.issuer.signed_key_claims": {
        "arguments": "key: str",
        "returns": "KeyClaims | None",
        "typescript": "signedKeyClaims"
      },
      "remember.mcp_tools._definitions.memory_tools": {
        "arguments": "",
        "returns": "tuple[ToolDefinition, ...]",
        "typescript": "memoryTools"
      },
      "remember.mcp_tools._definitions.render_tools_list": {
        "arguments": "tools: Iterable[ToolDefinition], *, project: bool, path_ingest: bool, read_only: bool",
        "returns": "list[dict[str, object]]",
        "typescript": "renderToolsList"
      },
      "remember.mcp_tools._definitions.tool": {
        "arguments": "name: str",
        "returns": "ToolDefinition",
        "typescript": "tool"
      },
      "remember.mcp_tools._errors.error_result": {
        "arguments": "error: ToolError",
        "returns": "dict[str, object]",
        "typescript": "errorResult"
      },
      "remember.mcp_tools._errors.invalid_arguments": {
        "arguments": "*, detail: str",
        "returns": "ToolError",
        "typescript": "invalidArguments"
      },
      "remember.mcp_tools._errors.map_error": {
        "arguments": "error: BaseException",
        "returns": "ToolError",
        "typescript": "mapError"
      },
      "remember.mcp_tools._query.validate_saved_query_identifier": {
        "arguments": "*, value: object, field: str",
        "returns": "str",
        "typescript": "validateSavedQueryIdentifier"
      },
      "remember.mcp_tools._validate.validate_arguments": {
        "adaptation": "No settings/path_ingest or implicit MCP environment; path requires injected resolver owned by MCP host. See design \u00a77.",
        "arguments": "name: str, arguments: Mapping[str, object], *, path_ingest: bool=False, settings: McpMemorySettings | None=None, max_body_bytes: int | None=None",
        "returns": "dict[str, object]",
        "typescript": "validateArguments",
        "typescriptArguments": "{name: string, arguments: Record<string, unknown>, pathResolver?: PathBodyResolver, maxBodyBytes?: number}",
        "typescriptReturns": "Promise<Record<string, unknown>>"
      }
    },
    "memoryToolCatalogue": {
      "contract": "all definitions, permissions, versions, routes, annotations, schemas and validators/error mapping; no SDK-owned host I/O, optional injected path resolver",
      "definitionFields": "remember.mcp_tools._definitions.ToolDefinition",
      "exports": {
        "ADJACENT_CHUNKS_TOOL_NAME": {
          "disposition": "public client support export",
          "typescript": "ADJACENT_CHUNKS_TOOL_NAME"
        },
        "DELETE_DOCUMENT_TOOL_NAME": {
          "disposition": "public client support export",
          "typescript": "DELETE_DOCUMENT_TOOL_NAME"
        },
        "DOCUMENT_REFERENCES_TOOL_NAME": {
          "disposition": "public client support export",
          "typescript": "DOCUMENT_REFERENCES_TOOL_NAME"
        },
        "DocumentDeleteBackend": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "DocumentReferencesBackend": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "DocumentSearchBackend": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "INGEST_TOOL_NAME": {
          "disposition": "public client support export",
          "typescript": "INGEST_TOOL_NAME"
        },
        "MEMORY_WRITE_TOOL_NAMES": {
          "disposition": "public client support export",
          "typescript": "MEMORY_WRITE_TOOL_NAMES"
        },
        "McpMemorySettings": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "MemoryWriteBackend": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "OPEN_QUERY_TOOL_NAMES": {
          "disposition": "public client support export",
          "typescript": "OPEN_QUERY_TOOL_NAMES"
        },
        "OPERATION_TOOL_NAMES": {
          "disposition": "public client support export",
          "typescript": "OPERATION_TOOL_NAMES"
        },
        "PIPELINE_READINESS_TOOL_NAME": {
          "disposition": "public client support export",
          "typescript": "PIPELINE_READINESS_TOOL_NAME"
        },
        "PROJECT_ARGUMENT": {
          "disposition": "public client support export",
          "typescript": "PROJECT_ARGUMENT"
        },
        "Permission": {
          "disposition": "public client support export",
          "typescript": "Permission"
        },
        "SEARCH_DOCUMENTS_TOOL_NAME": {
          "disposition": "public client support export",
          "typescript": "SEARCH_DOCUMENTS_TOOL_NAME"
        },
        "SECTION_HISTORY_TOOL_NAME": {
          "disposition": "public client support export",
          "typescript": "SECTION_HISTORY_TOOL_NAME"
        },
        "SectionHistoryBackend": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "ToolArgumentError": {
          "disposition": "public client support export",
          "typescript": "ToolArgumentError"
        },
        "ToolDefinition": {
          "disposition": "public client support export",
          "typescript": "ToolDefinition"
        },
        "ToolError": {
          "disposition": "public client support export",
          "typescript": "ToolError"
        },
        "error_result": {
          "disposition": "public client support export",
          "typescript": "errorResult"
        },
        "handle_delete_document_tool": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "handle_document_references_tool": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "handle_memory_write_tool": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "handle_search_documents_tool": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "handle_section_history_tool": {
          "disposition": "separate MCP library: host/backend execution contract; no base-client export"
        },
        "invalid_arguments": {
          "disposition": "public client support export",
          "typescript": "invalidArguments"
        },
        "map_error": {
          "disposition": "public client support export",
          "typescript": "mapError"
        },
        "memory_tools": {
          "disposition": "public client support export",
          "typescript": "memoryTools"
        },
        "render_tools_list": {
          "disposition": "public client support export",
          "typescript": "renderToolsList"
        },
        "tool": {
          "disposition": "public client support export",
          "typescript": "tool"
        },
        "validate_arguments": {
          "disposition": "public client support export",
          "typescript": "validateArguments"
        },
        "validate_saved_query_identifier": {
          "disposition": "public client support export",
          "typescript": "validateSavedQueryIdentifier"
        }
      },
      "source": "remember.mcp_tools"
    },
    "types": {
      "remember.connection.Connection": {
        "fields": {
          "api_url": {
            "annotation": "str | None",
            "default": null
          },
          "api_url_source": {
            "annotation": "Source | None",
            "default": null
          },
          "claims": {
            "annotation": "KeyClaims | None",
            "default": null
          },
          "issuer": {
            "annotation": "str | None",
            "default": null
          },
          "key": {
            "annotation": "SecretStr | None",
            "default": null
          },
          "key_source": {
            "annotation": "Source | None",
            "default": null
          },
          "mcp_url": {
            "annotation": "str | None",
            "default": null
          },
          "project": {
            "annotation": "str | None",
            "default": null
          },
          "stored": {
            "annotation": "StoredCredentials | None",
            "default": null
          }
        },
        "methods": {
          "authorization": {
            "arguments": "self",
            "returns": "str | None",
            "typescript": "authorization"
          }
        },
        "typescript": "Connection"
      },
      "remember.connection.ResolvedProject": {
        "fields": {
          "api_url": {
            "annotation": "str",
            "default": "Field(min_length=1)"
          },
          "name": {
            "annotation": "str",
            "default": null
          },
          "project": {
            "annotation": "str",
            "default": "Field(min_length=1)"
          }
        },
        "methods": {},
        "typescript": "ResolvedProject"
      },
      "remember.credentials.StoredCredentials": {
        "fields": {
          "api_url": {
            "annotation": "str | None",
            "default": "None"
          },
          "default_project": {
            "annotation": "str | None",
            "default": "Field(default=None, min_length=1, max_length=200)"
          },
          "expires_at": {
            "annotation": "datetime | None",
            "default": "None"
          },
          "issuer": {
            "annotation": "str | None",
            "default": "None"
          },
          "key": {
            "annotation": "SecretStr | None",
            "default": "None"
          },
          "key_id": {
            "annotation": "str | None",
            "default": "None"
          },
          "version": {
            "annotation": "Literal[2]",
            "default": null
          }
        },
        "methods": {},
        "typescript": "StoredCredentials"
      },
      "remember.issuer.IssuerMetadata": {
        "fields": {
          "device_authorization_endpoint": {
            "annotation": "str | None",
            "default": "None"
          },
          "issuer": {
            "annotation": "str",
            "default": null
          },
          "jwks_uri": {
            "annotation": "str | None",
            "default": "None"
          },
          "remember_account_endpoint": {
            "annotation": "str | None",
            "default": "None"
          },
          "remember_mcp_endpoint": {
            "annotation": "str | None",
            "default": "None"
          },
          "remember_project_endpoint": {
            "annotation": "str | None",
            "default": "None"
          },
          "revocation_endpoint": {
            "annotation": "str | None",
            "default": "None"
          },
          "token_endpoint": {
            "annotation": "str | None",
            "default": "None"
          }
        },
        "methods": {
          "endpoint": {
            "arguments": "self, name: Literal['device_authorization_endpoint', 'token_endpoint', 'revocation_endpoint', 'remember_project_endpoint', 'remember_account_endpoint', 'remember_mcp_endpoint']",
            "returns": "str",
            "typescript": "endpoint"
          }
        },
        "typescript": "IssuerMetadata"
      },
      "remember.issuer.KeyClaims": {
        "fields": {
          "exp": {
            "annotation": "int | None",
            "default": "None"
          },
          "iss": {
            "annotation": "str",
            "default": "Field(min_length=1)"
          },
          "jti": {
            "annotation": "str | None",
            "default": "None"
          },
          "org": {
            "annotation": "str | None",
            "default": "None"
          },
          "permissions": {
            "annotation": "list[str]",
            "default": "Field(default_factory=list)"
          },
          "projects": {
            "annotation": "list[str] | Literal['org:*'] | None",
            "default": "None"
          },
          "sub": {
            "annotation": "str | None",
            "default": "None"
          }
        },
        "methods": {
          "covers": {
            "arguments": "self, project_id: str",
            "returns": "bool",
            "typescript": "covers"
          },
          "expires_at": {
            "arguments": "self",
            "returns": "datetime | None",
            "typescript": "expiresAt"
          }
        },
        "typescript": "KeyClaims"
      },
      "remember.mcp_tools._definitions.ToolDefinition": {
        "fields": {
          "description": {
            "annotation": "str",
            "default": null
          },
          "destructive": {
            "annotation": "bool",
            "default": "False"
          },
          "http_route": {
            "annotation": "str",
            "default": null
          },
          "input_schema": {
            "annotation": "dict[str, object]",
            "default": null
          },
          "name": {
            "annotation": "str",
            "default": null
          },
          "permission": {
            "annotation": "Permission",
            "default": null
          },
          "tool_version": {
            "annotation": "int",
            "default": null
          }
        },
        "methods": {
          "annotations": {
            "arguments": "self",
            "returns": "dict[str, bool]",
            "typescript": "annotations"
          },
          "mutates": {
            "arguments": "self",
            "returns": "bool",
            "typescript": "mutates"
          }
        },
        "typescript": "ToolDefinition"
      },
      "remember.mcp_tools._errors.ToolArgumentError": {
        "fields": {},
        "methods": {
          "__init__": {
            "arguments": "self, *, error: ToolError",
            "returns": "None",
            "typescript": "constructor"
          }
        },
        "typescript": "ToolArgumentError"
      },
      "remember.mcp_tools._errors.ToolError": {
        "fields": {
          "agent_action": {
            "annotation": "str",
            "default": null
          },
          "code": {
            "annotation": "str",
            "default": null
          },
          "detail": {
            "annotation": "str",
            "default": null
          },
          "reason_code": {
            "annotation": "str | None",
            "default": "None"
          },
          "request_id": {
            "annotation": "str | None",
            "default": "None"
          },
          "retry_after": {
            "annotation": "float | None",
            "default": "None"
          },
          "retryable": {
            "annotation": "bool",
            "default": null
          },
          "status_code": {
            "annotation": "int | None",
            "default": null
          }
        },
        "methods": {
          "as_dict": {
            "arguments": "self",
            "returns": "dict[str, object]",
            "typescript": "asDict"
          }
        },
        "typescript": "ToolError"
      }
    }
  }
};
