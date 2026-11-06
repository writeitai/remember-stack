**Engine PR #502 at `f6df35b28d64843a0e870992d4c39977191f078b`: CHANGES_REQUESTED.** The `validateArguments` finding from round 3 is fixed. But the same diff adds an error-mapping row that sends a request timeout to the wrong error code, which contradicts Python and the published MCP docs. The fix is one table cell.

I made no edits, commits, pushes or comments. I looked only at `b540c078..f6df35b2`. No Python source has changed since `1dc23f21`, and `typescript_client_parity.json` still parses.

## Round-3 finding (`validateArguments` path mode): fixed
- **Manifest** (`typescript_client_parity.json:400-407`): it keeps the exact Python signature as the drift input. It adds `typescriptArguments` `{name, arguments, pathResolver?, maxBodyBytes?}`, `typescriptReturns: Promise<…>`, and an `adaptation` note. `McpMemorySettings` stays in the MCP library only. The catalogue `contract` text (`:835`) now names the injected-resolver exception instead of claiming "pure … no host I/O".
- **Design** (`typescript_client_design.md:290` table row and `:341-356` prose): the base client has no `settings` or `pathIngest` parameter and never loads `REMEMBERSTACK_MCP_*`. A path with no resolver is refused with `path_not_allowed`. Base validation checks that the body is non-empty and within the size limit. The MCP library owns the one resolver and its release-gated security fixtures. Nothing contradicts this anywhere else in the doc.
- **It matches Python:**
  - `settings` is only used in the path branch (`_memory.py:512` → `_resolve_path_body`). Dropping it changes nothing for text or base64 bodies.
  - The resolver's inputs and outputs (path, filename, mime, cap in; content, filename, mime out) match `_resolve_path_body` (`_memory.py:584-804`), including the size-cap fallback and MIME taken from the real file name (`resolved.name`).
  - `path_not_allowed` is already a published code meaning "Path ingest is off…" (`website/…/reference/mcp/page.mdx:456`).
- **Nits:** `environmentIssuer` is added (`parity.json:428-432`) and matches `connection.py:90`. `__init__` is now mapped to `constructor`.

## New material contradiction: `RequestTimeoutError` → `engine_unavailable`
The new row at `typescript_client_design.md:291` says `mapError` reports `RequestTimeoutError` as `engine_unavailable`. Its premise is that these errors have no exact Python class. That premise is wrong for this error:
- **The design itself** (`:316`) defines `RequestTimeoutError` as a `MemoryApiError` with `statusCode=0`.
- **Python raises exactly that on a request timeout.** Any `httpx.HTTPError` becomes `MemoryApiError(status_code=0, …)` (`client.py:1011, 1031-1033`). `_map_http_error` then sends status 0 to `transport_error` (`_errors.py:229-232`). Python's `map_error` docstring and its `TimeoutError` branch (`_errors.py:193`) say the same. I ran it: `map_error(MemoryApiError(status_code=0, …))` and `map_error(TimeoutError())` both return `transport_error 0 True`.
- **The published code table** (`mcp/page.mdx:467,470`, `errors/page.mdx:217-218`) defines `transport_error` as status 0, "No answer from the deployment". It defines `engine_unavailable` as "the 5xx status".

Following the row would give `engine_unavailable` with `status_code` 0, a pair the published table never allows. A TypeScript host would report a timeout differently from a Python host, and `mapError` would treat two errors that both have `statusCode=0` differently.

**Fix:** map `RequestTimeoutError` to `transport_error` (`status_code` 0, retryable), exactly as Python maps `MemoryApiError(status_code=0)`. It can then come out of the "no Python exact class" row, with a fixture that compares it against Python. The `AbortError` → `cancelled` and `NumericPrecisionError` cells can stay as they are.

## Non-blocking notes (for the separate MCP-library design)
- **Path ingest on the HTTP transport.** Python's HTTP transport always passes `path_ingest=False` (`mcp_http.py:299`); only stdio passes `True` (`cli.py:783`). So the HTTP server never reads local files, even when ingest roots are configured. TypeScript keeps `pathIngest` on `renderToolsList` but uses resolver presence in validation. The MCP-library design should bind "inject a resolver only when rendering with `pathIngest=true` (stdio)", so the tool list and validation can't disagree. A fixture should also pin the stated difference: a path with no resolver gives `path_not_allowed`, where Python with `path_ingest=False` gives `invalid_arguments` ("Unknown argument keys: path").
- **`invalid_response` is not in the published MCP code table.** Python's closest code is `local_backend_error` ("the deployment's answer did not match the expected shape"). Either reuse that code or add `invalid_response` to the docs when the TypeScript MCP library ships.
- **`cancelled` already exists** as a SQL query code (`errors/page.mdx:154`). Its retryable flag stays false, which is consistent, but the overlap is worth a sentence.

I treated CLA assent as a contributor merge gate, not a design finding. Cloud PR #714 at `da143d27` was approved last round, and this diff doesn't change that.
