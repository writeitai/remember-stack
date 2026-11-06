**APPROVED. Engine PR #502 at `c2d302a66a75dd9110b379874ebfdeb6a648ebdc`.**

I only looked at the binding row and paragraph in `f6df35b2..c2d302a6`. The review report and dispositions are history and weren't checked. No Python file has changed since `1dc23f21`. I made no edits, commits, comments or pushes.

All four corrections now match Python. I ran each case against the real Python code:

- **Timeout (the finding left over from round 4): fixed.** `plan/designs/typescript_client_design.md:361-362` now maps `RequestTimeoutError` to `transport_error` with status 0 and a retry hint. Python gives the same: `map_error(MemoryApiError(status_code=0))` returns `transport_error 0 True` (`_errors.py:229-232`, `_transport_error`). This matches the class table (`:316`, `RequestTimeoutError: statusCode=0`) and the published code table (`mcp/page.mdx:467`). Line 291 no longer puts the timeout in the "no Python class" row.
- **Cancellation and precision row (`:291`).** It now covers only `AbortError` and `NumericPrecisionError`:
  - `AbortError` becomes `cancelled` with no status and no retry. Python does the same for a cancelled query (`SandboxRejection(CANCELLED)`), because `cancelled` is not in `_RETRYABLE_QUERY_CODES`.
  - `NumericPrecisionError` becomes `local_backend_error`, not retryable. Python maps a response that fails its model check the same way (`ValidationError` → `local_backend_error None False`, `_errors.py:170-178`). That is a published code (`mcp/page.mdx:471`). `invalid_response` no longer appears anywhere in the design or the manifest.
- **Path with no resolver (`:347-348`).** It now gives `invalid_arguments`. Python with `path_ingest=False` gives `invalid_arguments None "Unknown argument keys: path."` (`_memory.py:413-415` → `reject_unknown_keys`, `:1037-1045`). The manifest's adaptation note (`typescript_client_parity.json:406`) doesn't name a code, so nothing contradicts this. The leftover `path_not_allowed` cases (outside the allowed directories, NUL byte) belong to the resolver itself, as in Python.
- **Future MCP release gate (`:358-360`).** A path resolver is injected only for stdio when tools are rendered with `pathIngest=true`. HTTP hosts never get one, and fixtures must show the tool list and validation agree. This matches Python: stdio passes `path_ingest=True` (`cli.py:783`) and HTTP passes `False` (`mcp_http.py:299`).

Two small points for the implementation review, not for this design:

1. `NumericPrecisionError` is a subclass of `MemoryApiError`. So `mapError` has to check for it before the general `MemoryApiError` branch, and its result should have status null, as Python's `local_backend_error` does. The `:291` fixture should pin that status.
2. The detail text for a path with no resolver should match Python's "Unknown argument keys: path." Python also rejects the unknown key before it checks whether more than one body was given, so `{path, text}` gets the same refusal.

The cloud PR #714 approval at `da143d27` still stands. The CLA remains the contributor's merge gate.
