# TypeScript client parity and contract drift

**Date:** 2026-10-02. **Status:** non-binding analysis.

## Question and owner direction

The owner selected `@rememberdev` and requested a TypeScript equivalent of the
PyPI `remember` library covering its full client scope, separate CLI and MCP
libraries, design review by Claude Opus 5.5 before implementation, and a second
Opus review before merge. Registry ownership has not been verified; approval
and merge are not npm publication.

## Evidence inspected

The published Python baseline is `remember==0.17.2`, tag
`dd0c78015099cdd84ab5f3501744d6e225125989` in `writeitai/remember-stack`.
Source main inspected: `1dc23f21f24ec5f22ec4e51c751363b3658df7a6`.
The following paths are relative to that repository, not this cloud repo:

- `src/remember/__init__.py`: public exports, models, errors and connection resolver.
- `src/remember/client.py`: every public MemoryClient, Client and AccountApi method.
- `src/remember/connection.py`: precedence, issuer routing, pinned project and stored-key origins.
- `src/remember/credentials.py`: version-2 credentials, path selection, owner-only reads.
- `src/remember/issuer.py`: JWS recognition, unverified routing claims, HTTPS/loopback, metadata and redirects.
- `src/remember/models.py`, `src/remember/query_sandbox/result.py`: exact typed response contracts and validation.
- `src/remember/mime.py`: deterministic converter MIME mappings.
- `src/remember/mcp_tools/`: complete 16-tool catalogue, permissions, annotations, validators and error mappings; seven query tools are the SDK dispatch subset.
- `scripts/export_openapi.py`, `openapi.json`, `src/tests/surfaces/test_openapi_export.py`: offline served-profile specification and absent connector routes.
- `src/tests/surfaces/test_client_sdk.py`, `test_client_connection.py`: wire and routing behavior.

Cloud-side counterparts (private; the engine authority is D140/D136): D81 and `design/designs/canonical-remember-python-distribution.md`;
D90 and `design/designs/remember-dev-api-key-and-mcp.md` §9; D59's public-search
privacy rule in `design/designs/data-plane-auth-perimeter.md`. The 2026-08
`client-sdk-cli-mcp-packaging-remember-dev.md` is historical analysis and its
two-package npm recommendation does not bind.

## Inventory (Python arguments shown; TypeScript options use camelCase)

This is the inspected engine-main inventory. `search_documents` and
`search_documents_request` are additions after published 0.17.2; all other
listed methods already occur in that release. Cover both those additions and
the complete published baseline.

| Python public method | TypeScript method/property | Python arguments |
| --- | --- | --- |
| `MemoryClient.close` | `close` | `—` |
| `MemoryClient.list_operations` | `listOperations` | `—` |
| `MemoryClient.run_operation` | `runOperation` | `name, arguments` |
| `MemoryClient.query_sql` | `querySql` | `sql, parameters, max_rows` |
| `MemoryClient.open_query` | `openQuery` | `sql, parameters, max_rows` |
| `MemoryClient.explain_query` | `explainQuery` | `sql, parameters` |
| `MemoryClient.facts_context` | `factsContext` | `query, time, hops, predicate, entity_ids` |
| `MemoryClient.combined_context` | `combinedContext` | `query, time` |
| `MemoryClient.claims_and_sources_context` | `claimsAndSourcesContext` | `query` |
| `MemoryClient.resolve_entity` | `resolveEntity` | `name` |
| `MemoryClient.explain_sql` | `explainSql` | `sql, parameters` |
| `MemoryClient.describe_query_space` | `describeQuerySpace` | `pattern, include_examples` |
| `MemoryClient.search_query_space` | `searchQuerySpace` | `query, k` |
| `MemoryClient.list_saved_queries` | `listSavedQueries` | `namespace, status` |
| `MemoryClient.describe_saved_query` | `describeSavedQuery` | `namespace, name, version` |
| `MemoryClient.run_saved_query` | `runSavedQuery` | `namespace, name, parameters, version, max_rows` |
| `MemoryClient.call_open_query` | `callOpenQuery` | `name, arguments` |
| `MemoryClient.resolve` | `resolve` | `name, context_entity_ids` |
| `MemoryClient.lookup_relations` | `lookupRelations` | `subject_entity_id, predicate, object_entity_id, valid_at, k` |
| `MemoryClient.transcript_relation` | `transcriptRelation` | `relation_id` |
| `MemoryClient.lookup_observations` | `lookupObservations` | `entity_id, property_query, k` |
| `MemoryClient.search_claims` | `searchClaims` | `query, k, channel, documents` |
| `MemoryClient.search_chunks` | `searchChunks` | `query, k, channel, documents` |
| `MemoryClient.adjacent_chunks` | `adjacentChunks` | `chunk_id, window` |
| `MemoryClient.hydrate_relation` | `hydrateRelation` | `relation_id` |
| `MemoryClient.graph_neighborhood` | `graphNeighborhood` | `entity_id, hops, predicates, valid_at, believed_at, limit, continuation, include_paths` |
| `MemoryClient.graph_path` | `graphPath` | `from_entity_id, to_entity_id, max_hops, predicates, valid_at, believed_at` |
| `MemoryClient.graph_citation_path` | `graphCitationPath` | `from_doc_id, to_doc_id, max_hops` |
| `MemoryClient.deployment_build_info` | `deploymentBuildInfo` | `—` |
| `MemoryClient.pipeline_readiness` | `pipelineReadiness` | `version_ids, require` |
| `MemoryClient.wait_for_readiness` | `waitForReadiness` | `version_ids, timeout, poll_interval, require_p3` |
| `MemoryClient.ingest` | `ingest` | `source, content, filename, mime, title, source_kind, source_ref, source_modified_at, versioning_mode, source_version_ref, source_path` |
| `MemoryClient.list_documents` | `listDocuments` | `limit, cursor, status` |
| `MemoryClient.search_documents` | `searchDocuments` | `query, filters, versions, k, cursor` |
| `MemoryClient.search_documents_request` | `searchDocumentsRequest` | `request` |
| `MemoryClient.delete_document` | `deleteDocument` | `doc_id` |
| `MemoryClient.connectors` | `connectors` | `—` |
| `MemoryClient.add_connector` | `addConnector` | `connector` |
| `MemoryClient.pause_connector` | `pauseConnector` | `connector_id` |
| `MemoryClient.connector_status` | `connectorStatus` | `connector_id` |
| `Client.from_env` | `fromEnv` | `—` |
| `Client.account` | `account` | `—` |
| `Client.ingest` | `ingest` | `source, content, filename, mime, title, source_kind, source_ref, source_modified_at, versioning_mode, source_version_ref, source_path` |
| `Client.ingest_file` | `ingestFile` | `file_path, filename, mime, title, source_kind, source_ref, source_modified_at, versioning_mode, source_version_ref, source_path` |
| `AccountApi.whoami` | `whoami` | `—` |
| `AccountApi.get` | `get` | `path, params` |

Every exported Pydantic model, enum/type alias and error is also part of the
client inventory. Runtime-specific constructs differ: Python UUIDs/datetimes
become UUID/ISO strings (Date input is accepted), tuple results become arrays,
context-manager teardown becomes `close`/async disposal, and Path becomes a
Node path. QueryResult rows remain accessible as a property. CLI entry points,
MCP hosting, device-login and credential-writing tools are separate package
responsibilities, not missing client methods.

## Alternatives and recommendation

1. A completely handwritten SDK is initially short but duplicates DTOs and
   makes unnoticed route/schema changes likely. Reject it.
2. A wholly generated SDK exposes HTTP rather than the Python client: it lacks
   project routing, owner-only credentials, file MIME inference, readiness
   waits and typed failure behavior. Keep generation underneath a facade.
3. Separate cloud and memory npm clients repeat the selection and release
   coordination that D81 removed. One client, account namespace.
4. A standalone TypeScript repository creates a third contract publisher.
   Put `packages/typescript-client` in the engine repository beside Python.

Generate models and request metadata from authoritative schemas, then write a
small typed facade for the complete inventory. Use `openapi-typescript-codegen`
for generation, matching the family convention, despite its maintenance cost:
its author marks it unmaintained and recommends a different generator.
This decision does not authorize replacing the FE generator. Unsupported
OpenAPI constructs must fail or get a tested deterministic projection; never
silently lose nullable/union fields.

A schema snapshot check alone cannot detect changed Python behavior, nor does
checking a pinned cloud schema tell us about newer cloud API revisions. Need
local offline export checks, reproducible generation, Python public inventory
comparison, shared executable wire fixtures and a networked upstream-cloud
provider compatibility check run from the private cloud repository. The engine
exports only the issuer-independent public contract it consumes; no private
cloud schema enters this repository. Report fetch failures as
failures. Upstream additions require explicit supported/excluded disposition.

## Runtime and failure tradeoffs

Node.js 22+ is the supported runtime, ESM and CommonJS distribution. A browser
package cannot offer local file and owner-only credential parity; browsers
should use a backend. Node fetch cannot reliably identify a network failure
that happened before a write reached its server. Therefore do not replay a
write on network failure, even though Python can retry a proven connect error.
Re-resolve a moved key-routed host and retry reads once only when its URL
changed; the pinned project identity must not follow a changed default. A
421 refusal can safely retry once after a changed resolved URL.

**Superseded exploration:** that last 421 conclusion is replaced by D140: only
classified reads may retry; writes never replay, including a 421 refusal.

**Superseded exploration (D140 keeps Python's GET/POST split):**
Public search uses POST bodies, including unfiltered search: Python's unfiltered
GET behavior is not a reason to put customer terms into URLs. This explicit
wire difference preserves search semantics and the current privacy authority.

Cost: no added hosted service or database; development/test dependencies only,
plus lightweight schema validation at runtime. CI needs Python schema export
and a Node build, plus Windows/Linux credential and consumer tests. Full engine
or LLM processing is unnecessary for SDK parity checks. Native Python model
validators are not wholly representable in JSON Schema: adversarial fixtures
cover those invariants alongside schema validation.

## External sources (retrieved 2026-10-02)

- https://pypi.org/pypi/remember/json — version 0.17.2, lightweight HTTP/Pydantic distribution.
- https://github.com/ferdikoomen/openapi-typescript-codegen — generation, custom request option, union types; maintenance warning.
- https://nodejs.org/api/packages.html#conditional-exports — separate import/require exports and encapsulation.
- https://docs.npmjs.com/trusted-publishers/ — publication identity/configuration is separate from building and merging.
- https://docs.npmjs.com/policies/disputes/ — names must be for active use; defensive empty reservations are not the plan.

## Opus review dispositions and revised choices

The first Opus review requested four blocking corrections: engine-owned binding
design; issuer-agnostic provider checks rather than private schema copying; an
exact SHA-based normative inventory; and no write replay on 421. All are adopted.
Version 0.17.2 alone is insufficient because current main has that version string
but additional document-search methods, document filters, source-path input and
DocumentSearch exports. The normative inventory is a binding JSON artifact.

Keep openapi-typescript-codegen 0.30.0 as type generation only, after an explicit
OpenAPI 3.1 → 3.0 projection for const/null unions and binary schema. Its generated
HTTP transport cannot replace client routing; the facade uses generated request
metadata and model types, with custom binary upload handling. Alternatives are
openapi-typescript (native 3.1 types but no services), Hey API (maintained generator
but different family tooling), and a browser-capable core. They remain viable
proposals with explicit adoption triggers, not automatic migrations.

Node JSON precision is a correctness boundary: typed errors reject unsafe integer
responses/parameters before rounding or sending. JSON Schema output uses the
2020-12 dialect; defaults are filled without coercing arbitrary input types.
Lax Pydantic coercions are documented language differences, not silent behavior.
Errors compare class/code, never repr text. Every original review finding is
resolved in the binding contract/adaptation table or the build-order plan.

## Generator experiment (2026-10-02)

Executed openapi-typescript-codegen@0.30.0 against a projected 3.0.3 probe with
a required constant discriminator, required nullable string, nullable
string/integer union and binary property. Generated Probe.ts preserved
`kind: 'exact'`, `nullable: string | null`, nullable union and Blob binary.
This validates the projection approach, not full schema coverage: implementation
must exercise the real engine null/const/union/binary schemas and declaration
consumers, retain original 2020-12 runtime schemas, and fail unsupported projections.

The post-0.17.2 differences also include filtered claims/chunks search, ingest
source_path, and four DocumentSearch exports, not only two new methods.

## Opus round 2 disposition

The remaining material finding was the incomplete catalogue/support inventory.
The binding design now requires all 16 tools and their permissions/annotations;
the normative JSON inventories all 28 mcp_tools exports with client versus
MCP-host dispositions, exact pure helper signatures, Connection return/fields
and credential/issuer helpers. Minor findings explicitly name route_scope.py,
pin the MIME fallback, distinguish cancellation/request/readiness timeout
classes and limit structured diagnostics to query errors. Superseded retry
and POST-only exploration above is labelled rather than treated as current.

## Opus round 3 disposition

Round 3 verified the complete catalogue/support manifest but identified that
Python validate_arguments is not pure in path mode and consumes host settings.
Choose its Option B: the base receives an explicit injected path resolver,
never MCP settings/environment; the separate MCP package owns the single
Python-equivalent security resolver and release-gated fixtures. This avoids
pulling host configuration into a library that only provides the client and
catalogue. The normative manifest preserves Python signatures as drift inputs
and specifies this TypeScript signature/behavior adaptation explicitly.
Additional nits add environmentIssuer, correct constructor naming, and state
structured mappings for cancellation/request timeout/numeric precision errors.

## Opus round 4 disposition

The injected resolver contract is approved in substance. Correct the timeout
error mapping to the existing transport_error/status 0 pair, matching Python.
Reuse local_backend_error for unsafe response precision; explicitly match
Python's invalid_arguments for a path with no resolver, and require the future
MCP package to keep resolver injection consistent with stdio tool rendering.


The runtime transport choice above is superseded by the measured Fetch 421
evidence in [typescript_transport_421.md](typescript_transport_421.md). D140
now selects a single-transmission Node HTTP/HTTPS adapter, borrowed-agent or
request-transport injection, and explicit total-deadline/proxy adaptations.

## General datetime inputs: Python client versus the published format

Observed 2026-10-02 while reconciling implementation review round 2: Python's
`lookup_relations`, `graph_neighborhood` and `graph_path` accept naive datetime
objects and emit offset-free ISO strings; they also forward non-UTC offsets
unchanged (`src/remember/client.py`, those three methods). Their published
OpenAPI fields use `format: date-time` and omit the runtime UTC constraint.
Executed Python client wire cases therefore differ from the SDK's unchanged
AJV date-time assertion, which requires an offset.

The server has a stricter boundary than its Python client. In
`src/remember/http_api.py`, `_require_utc`/`UTCInstant`, the `valid_at` lookup
parameter and the graph request models refuse naive and non-UTC instants with
422. This shipped in v0.17.1/v0.17.2 (G27, commit 78660f99); v0.16.0 used plain
datetime fields. `src/tests/surfaces/test_http_api_robustness.py`, UTC lookup
and graph/path boundary cases, require those refusals. The validator's UTC
requirement is absent from the generated format annotation.

[RFC 3339 §5.6](https://www.rfc-editor.org/rfc/rfc3339#section-5.6)
defines full-time with a time offset. [JSON Schema validation §7](https://json-schema.org/draft/2020-12/json-schema-validation#section-7)
defines date-time and distinguishes format annotation from optional assertion
(retrieved 2026-10-02). The SDK performs that assertion for conformance.

The selected adaptation refuses naive TypeScript options locally as
InputValidationError. The current server would refuse them too; older servers
would interpret an unspecified timezone. Date inputs become UTC ISO. Offset
strings remain unchanged so the server remains the authority: +02:00 receives
the same 422 MemoryApiError as Python on v0.17.1+ and is accepted at the v0.16
boundary. Tests must distinguish client wire parity from deployed acceptance.
An alternative would permit naive values and add a separate source-runtime
conformance disposition; it adds ambiguity and weakens the common assertion.
UTC-only lineage/model fields stay UTC-only. This selects no further client
UTC-only rule for the general methods.
