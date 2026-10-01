# Re-review: TypeScript client design (round 2)

**Heads reviewed** (both match the open draft PRs on GitHub):

| PR | Head | Base |
| --- | --- | --- |
| Engine `writeitai/remember-stack` #502 | `2de736e23f3a78cdb510ee87ebb28b24ce44fb75` | `1dc23f21` |
| Cloud `ultimate-memory-cloud` #714 | `db670762b54679b416c39bb73983f0abe303cf66` | `6069b61d` |

I made no edits, commits, pushes or comments.

## Verdicts

- **Engine PR #502: CHANGES_REQUESTED.** One material finding is left; it is part of M9 from the first review, not fully fixed.
- **Cloud PR #714: APPROVED.** Nothing material is left. Merge it after the engine PR (see L6 below).

## How the original findings were resolved

| Finding | Status | Evidence |
| --- | --- | --- |
| H1: binding design lived only in the private cloud repo | Resolved | The engine now has D140, `plan/designs/typescript_client_design.md` and the parity JSON. Cloud D95 is cut down to provider, naming and publishing obligations, with an "Engine authority" link. Issue #501 exists. |
| H2: cloud-schema check couldn't run and exposed private APIs | Resolved | `get` returns `unknown` and `whoami` returns an object. The engine publishes only the issuer-metadata, `ResolvedProject` and `whoami` schemas. The private repo runs a one-way check against public engine artifacts, so it needs no secrets and can't deadlock. No private cloud paths or DTOs appear in the engine diff. |
| H3: inventory in the wrong place and wrong baseline | Mostly resolved; see finding 1 | The JSON names the source SHA. Every entry matches source `1dc23f21` exactly: an AST comparison found all public methods of `MemoryClient`/`Client`/`AccountApi` present, 0 signature or return-type differences, and the `__all__` export list identical. The injected `client` mode, `__version__`, query-result properties and the corrected test citation are all in. |
| H4: replaying writes after a 421 | Resolved | Writes are never replayed, including after a 421 or a connect error. The D136 entry banner, D136 §8.3 step 2 and its failure table, and cloud D90 all say so. Python's current replay at `client.py:1034-1041` is scheduled to change in the implementation PR (plan step 2). |
| M1: "read" undefined | Resolved | Read eligibility comes from the engine's `_READ_ROUTES` table. Timeouts and aborts never retry. A write failure refreshes the host only for the next call. |
| M2: D90 contradictions | Resolved | D90 §9.2 and §13 are amended. |
| M3: POST search drift | Resolved | Python's GET/POST split is kept. The design now states which content still travels in URLs. |
| M4: undeclared behaviour changes | Resolved | There is now an adaptation table, Windows refuses the stored credentials file as Python does, and the MIME and proxy/TLS rules are pinned down. |
| M5: exception mapping | Resolved | The mapping table's class hierarchy matches Python, including `IssuerError` under `MemoryApiError` and `CredentialError` as a `ValueError`. |
| M6: defaults and validation strictness | Resolved | Draft 2020-12 schemas, defaults filled in, defaulted fields typed as always present. |
| M7: number precision | Resolved | Unsafe integers are refused rather than rounded. Node's `JSON.parse` exposes the raw number text, so this can be built. |
| M8: generator | Resolved | Types only, after a 3.1 → 3.0 conversion; `0.30.0` exists and matches the cloud FE pin. The experiment is recorded and a proposal file holds the alternatives. |
| M9: what the client provides to the CLI/MCP packages | **Partly resolved; see finding 1** | |
| M10: real released engines | Resolved | Gate 7 adds TypeScript to the existing `compatibility-matrix.yml` (0.15.0, 0.16.0, candidate). |
| M11: Layer 5 entry gate and publish authority | Resolved | The cloud roadmap restores the Entry line with a scoped TypeScript exception. Publishing needs owner approval and D5 evidence. |
| L1–L7 | Resolved | Remaining nits are listed below. |

**The four exact scopes you asked about:**
- **Python public client inventory:** holds, apart from finding 1.
- **Separate CLI/MCP build gates:** hold. They appear in design §7, delivery plan step 7, D140, and the cloud roadmap.
- **No private cloud schemas in public source:** holds.
- **One-way provider contracts:** hold.
- **No replay of mutating requests:** holds.

## Engine PR #502: remaining material finding

**1. The exports promised to the CLI/MCP packages don't cover the shared tool catalogue and aren't in the normative inventory.**

- **Where:** `typescript_client_design.md:313-322`, `typescript_client_parity.json`, `plan/analysis/typescript_client_parity.md:27`.
- **What the design says:**
  - §7 limits the catalogue export to "tool definitions/input schemas/error shapes with tool lookup/argument validation for **the seven open-query dispatch names**".
  - It also says "MCP depends only on public client and catalogue APIs", and D140 says CLI/MCP depend "only on declared public support exports".
- **What Python actually has at `1dc23f21`:**
  - `remember.mcp_tools` defines **16** tools: ingest, pipeline_readiness, delete_document, search_documents, 4 assured operations, adjacent_chunks and 7 open-query tools.
  - It exports 28 names, including `memory_tools`, `tool`, `render_tools_list`, `validate_arguments`, `Permission`, the name constants, `ToolError` and `error_result`.
  - The read-only mode of the `remember mcp` bridge depends on the full catalogue: `mcp_bridge.py:345-358` filters with `tool(name).permission == "memory:read"` for every remote tool.
- **What happens if it stays like this:** a TypeScript MCP package built to the declared exports can't apply that read-only filter to the other nine tools. It would have to redefine tool permissions, which breaks D136 ("defined once … never redefines a memory tool"), or reopen D140.
- **Related gaps in the same contract:**
  - The JSON gives the `resolve_connection` arguments but not its return type `Connection` or that type's fields (`key`, `key_source`, `api_url`, `api_url_source`, `project`, `issuer`, `mcp_url`, `stored`, `claims`).
  - It doesn't list the credential and issuer helpers §7 promises, such as `credentials_path`, `load_credentials`, `StoredCredentials`, `fetch_issuer_metadata`, `require_secure_url` and `signed_key_claims`.
  - So the public surface the separate packages depend on has no drift gate, and its TypeScript naming isn't defined.
- **Fix:**
  - State that the client exports the complete D136 memory-tool catalogue, generated from `remember.mcp_tools`: all definitions, permissions and annotations, `render`/`lookup`, validators and error shapes.
  - Add the support-export surface to the normative inventory (or a companion manifest under the same gate 3): `remember.mcp_tools.__all__`, the `Connection` fields and return type, and the listed credential and issuer read helpers.
  - Correct analysis line 27 to match.

## Engine PR #502: low-severity cleanups (not blocking)

- **L1.** Two paragraphs in `plan/analysis/typescript_client_parity.md` now contradict the binding design and aren't labelled as superseded:
  - lines 133-139 say "A 421 refusal can safely retry once";
  - lines 141-143 say "Public search uses POST bodies, including unfiltered search".

  The design-corpus skill allows superseded reasoning in analysis only if it's clearly labelled. The cloud analysis does label it (line 165).
- **L2.** Two different `_READ_ROUTES` tables exist: `surfaces/route_scope.py` (the correct one, which includes POST `/readiness` and saved-query run) and `surfaces/http_api.py:1916` (the spend gate, which lacks them). Name `route_scope.py` in §4.
- **L3.** §5 still says unknown MIME types use "a deterministic portable mapping **or** `application/octet-stream`". The §7 table already decides this (pinned mime-db, then octet-stream); make §5 match.
- **L4.** No error type is given for a caller abort.
  - The exported `TimeoutError` class shares its name with the `DOMException` named `TimeoutError` that `AbortSignal.timeout` throws.
  - Fix: say what error an abort produces, and whether a readiness timeout can be told apart by class, not just by name.
- **L5.** It's unclear whether `retryable` and `requestId` are ever populated outside `/query/` routes. Python turns non-query error envelopes into a string (`client.py:1086-1087`), and cloud D90 §13 could be read as if SDKs surface D41 codes on every route. One sentence would settle it.

## Cloud PR #714: low-severity cleanups (not blocking)

- **L6.** `canonical-remember-python-distribution.md` links to `#d95--one-full-parity-typescript-client-in-the-rememberdev-npm-scope`. That anchor no longer exists; the heading's anchor is now `#d95--rememberdev-scope-and-cloud-provider-obligations-for-the-typescript-client`. D95's "Engine authority" links point at engine `main`, so they only work once #502 merges. Merge the engine PR first.

## Limitations

- I reviewed the design only. Nothing was built or run, apart from one Node check that `JSON.parse` exposes raw number text.
- I didn't check npm ownership of `@rememberdev`.
- I didn't check model fields against the generated schemas.
- I didn't recheck parts of D136 beyond §8 and its failure and test tables.
- I treated CLA assent as a contributor merge gate, as you asked, not as a technical finding.
