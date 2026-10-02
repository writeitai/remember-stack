**Engine PR #502 at `b540c07863b4092b60744b00f67291a91d052598`: CHANGES_REQUESTED.** The round-2 catalogue finding is fixed. But the new manifest contradicts itself in one narrow place, the `validateArguments` path-ingest mode.

**Cloud PR #714 at `da143d272140f93a4dad4fd3c55bdf03d4c9beaf`: APPROVED.**

I made no edits, commits, pushes or comments. I reviewed only the changes since `2de736e2` (engine) and `db670762` (cloud). No Python source has changed since `1dc23f21`, so all checks below run against that source.

## Engine #502

### Round-2 material finding: fixed
I compared every `supportExports` entry against the source using a script that parses the Python code (`/tmp/r3check.py`). All of these match exactly:
- **Connection functions:** `resolve_connection` now records `returns: "Connection"` and its TypeScript name (`typescript_client_parity.json:292-296`).
- **Connection type:** all 9 fields (`key` … `claims`) plus `authorization()` (`parity.json:426-470`).
- **Credential and issuer read helpers:** `config_dir`, `credentials_path`, `load_credentials`, `signed_key_claims`, `require_secure_url`, `fetch_issuer_metadata` and the other issuer/connection helpers. Also the `StoredCredentials`, `KeyClaims`, `IssuerMetadata` and `ResolvedProject` fields and methods.
- **Catalogue functions and types:** all 24 functions and 8 types, with exact arguments, defaults and return types. This includes `ToolDefinition` (`permission`, `tool_version`, `http_route`, `destructive`, `annotations()`, `mutates()`), `ToolError` and `ToolArgumentError`.
- **`remember.mcp_tools.__all__`:** all 28 names are present (`parity.json:717-828`):
  - 22 are public client support exports.
  - 6 go to the separate MCP library: the three backend protocols (`DocumentSearchBackend`, `DocumentDeleteBackend`, `MemoryWriteBackend`), `McpMemorySettings`, and the three `handle_*` handlers.
- **Design text:** §7 (`typescript_client_design.md:325-338`) now requires all 16 definitions (confirmed: there are 16 `ToolDefinition(` in `_definitions.py`). It also requires permissions, versions, routes, annotations and schemas, render/lookup, validators and error mapping.
- **Read-only filtering:** a TypeScript MCP host can now filter tools by permission exactly as `render_tools_list` does (`_definitions.py:797-798`).
- **Analysis:** line 27 is corrected.

### New material finding: `validateArguments` path mode
The manifest calls `validateArguments` a pure base-client export with "no host I/O", but the Python behaviour it must match reads files and needs a type the base client can't have.

**What the design says:**
- `parity.json:400-404` records `validate_arguments` as a "public client support export" with the exact signature `… path_ingest: bool=False, settings: McpMemorySettings | None=None, max_body_bytes …`.
- `parity.json:762-764` puts `McpMemorySettings` in the separate MCP library with "no base-client export".
- `parity.json:827` and design `:329,336-338` say "pure validators … no host I/O".
- Design `:273` says anything not in the §7 adaptation table must match Python.

**What Python does:**
- With `path_ingest=True`, `validate_arguments` reads local files under the configured roots: `_validate.py:95-101` → `parse_ingest_arguments` → `_resolve_path_body` (`_memory.py:512, 584`). That code holds the fail-closed path checks: allowed roots, symlink escape, regular-file check, size cap.
- With `settings=None`, it loads `REMEMBERSTACK_MCP_*` from the environment (`_validate.py:98`).
- Python's write handler reaches the same path logic only through private functions (`_memory.py:199` → `_run_ingest`), which no export covers.

**Why it matters:** the base client can't have that signature without importing a type from a library that depends on it. It can't match Python's path mode without host I/O. And the §7 table has no adaptation row for either. An implementer has to break one rule. Worse, the TypeScript MCP library might end up re-implementing security-sensitive path validation outside the catalogue, against D136's "defined once" rule.

**Fix (one paragraph plus one table row):**
- **Option A (recommended):** make the settings type and path-body resolution base catalogue exports. The base is Node-only and already reads local files and credentials. Change `McpMemorySettings` from the MCP library to a base export, and name the one exception to "no host I/O".
- **Option B:** keep settings in the MCP library. Add a §7 row saying base `validateArguments` takes an injected path resolver (or returns `path` unresolved), and the MCP library implements the `_memory.py:584` rules, with parity fixtures.

### Minor fixes: all verified
- **Superseded analysis labelled:** `typescript_client_parity.md:141-144` labels both the 421 retry text and the POST-only search text as superseded.
- **`route_scope.py` named:** design `:169-171`. The file has POST `/readiness` and saved-query run (`route_scope.py:69-70`), and the design separates it from the spend-gate table at `http_api.py:1916`.
- **MIME fallback fixed:** §5 (`:201-203`) now matches the §7 table: the pinned mime-db version, then `application/octet-stream`.
- **Distinct timeout and abort classes:** `:187-193` and the exception table `:313-314`:
  - caller cancellation → `AbortError`, with the signal reason as its cause;
  - request deadline → `RequestTimeoutError` (`MemoryApiError`, status 0);
  - readiness deadline → `TimeoutError` with the last report.

  They stay distinct even when the signal reason is a `DOMException` named `TimeoutError`.
- **Diagnostics only on query routes:** `:191-193` limits `retryable`/`requestId` to query errors.

### Low-severity nits (not blocking)
- `environment_issuer` (`connection.py:90`) is used by CLI login (`cli.py:915`, `setup.py:763`) but has no disposition in the manifest.
- Mapping `ToolArgumentError.__init__` to the TypeScript name `"Init"` (`parity.json:705-711`) should say "constructor".
- The design doesn't say how `mapError` in TypeScript maps the TypeScript-only `AbortError` and `NumericPrecisionError`. A direct port of `map_error` would turn an abort into `internal_error`. The MCP library design can settle this.

## Cloud #714

- **D95 anchor fixed:** `canonical-remember-python-distribution.md:101` now points to `#d95--rememberdev-scope-and-cloud-provider-obligations-for-the-typescript-client`. That matches the heading at `decisions.md:7563`, and no live document still uses the old anchor.
- **D90 §13 matches the engine:** `remember-dev-api-key-and-mcp.md:1393-1395` now says the diagnostics are surfaced for query routes only, and other routes keep HTTP status/detail behaviour. That agrees with engine design `:191-193`.
- **No new contradictions.** As before, merge the engine PR first, because D95's "Engine authority" links point at engine `main`.

I treated CLA assent as a contributor merge gate, not a design finding.
