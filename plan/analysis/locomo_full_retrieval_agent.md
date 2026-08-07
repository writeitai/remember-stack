# LoCoMo full-retrieval answer-agent analysis

**Status:** non-binding implementation analysis, 2026-08-07  
**Question:** what must the LoCoMo answer agent be able to read for the run to
measure the current RememberStack system rather than a three-operation subset?

## Problem

`RS-LoCoMo-Full-v10` builds and readiness-checks P2 and P3, but its answer
agent receives only the three assured-operation descriptors and dispatches
every call through `MemoryClient.run_recipe()`. P3 is explicitly integrity-only
and the nine open-query operations are used only by benchmark guards. That is a
valid measurement of the assured-operation layer, but it is not a measurement
of the full retrieval system on `main`.

The mismatch is visible in these authorities:

- `plan/designs/open_query_space_design.md` §§3.1 and 6 binds nine public
  query-infrastructure entry points in addition to the three assured
  operations.
- `decisions.md` D83 describes the shipping surface as the three assured
  operations **plus** those nine entry points.
- `src/rememberstack/surfaces/http_api.py`, `build_api()`, mounts seven direct
  read primitives before the assured and open-query surfaces.
- `src/rememberstack/surfaces/sdk.py`, `MemoryClient`, already exposes four of
  those primitives and all nine open-query operations; the three missing SDK
  veneers are ordinary HTTP parity work, not new retrieval behavior.
- `src/rememberstack/adapters/selfhost/mounts.py`, `LocalMountPublisher`, is the
  ordinary self-host mechanism for publishing the latest P3 snapshot as an
  atomic read-only filesystem tree.
- `src/rememberstack/core/consumption_skill.py`, `_mounts()`, tells an agent to
  prefer mounts for navigation, reading, and grep when they exist.

Therefore a score produced by v10 can answer “how good are the three assured
operations with this reader?” but cannot answer “how good is RememberStack's
current retrieval plane with an agent planning over it?”

## Scope of the complete read plane

The corrected answer seat gets every shipped, read-only retrieval capability
that a normal agent can use in this self-host composition:

1. Three assured operations: `resolve_entity`, `question_context`, and
   `current_context`.
2. Seven direct primitives: `resolve`, `lookup_relations`,
   `transcript_relation`, `lookup_observations`, `search_claims`,
   `search_chunks`, and `hydrate_relation`.
3. Nine open-query operations: `query_sql`, `explain_sql`, `query_cypher`,
   `explain_cypher`, `describe_query_space`, `search_query_space`,
   `list_saved_queries`, `describe_saved_query`, and `run_saved_query`.
4. Three ordinary filesystem motions over the published P3 mount: bounded
   list, literal grep, and text read.

This is access, not a requirement to call every channel on every question. The
caller remains the planner, as D50 requires. P1 is reachable through assured
operations, direct semantic/lexical primitives, and the allowlisted SQL search
functions. P2 is reachable through full Cypher. P3 is reachable as the
filesystem snapshot it was designed to be. PostgreSQL testimony and current
fact state are reachable through direct primitives and open SQL.

The following are deliberately outside the answer catalog:

- ingest, connector management, projection builds, readiness, and every other
  write or control operation;
- raw originals, because their reads require the attributed audit path and
  LoCoMo session markdown already supplies the benchmark source content;
- artifacts, because P3 and live source-chunk retrieval already expose the
  relevant readable corpus without adding an internal-store shortcut;
- Plane K, because the benchmark does not compose K and must not pretend an
  absent compiled layer exists;
- internal-only primitive names such as `fuse` or `rerank` that have no public
  call path.

## Options considered

### Keep v10 and only add SQL/Cypher

Rejected. It would still omit the direct evidence/audit primitives and the P3
filesystem. Calling that “full retrieval” would repeat the same category
error with a larger subset.

### Add benchmark-only database, object-store, or graph readers

Rejected. Private reads could bypass the query sandbox, D41/D48 confirmation,
typed envelopes, projection disclosure, and ordinary deployment boundaries.
The benchmark must use public API calls or the normal read-only mount.

### Add an HTTP P3 browsing API

Rejected. P3 is intentionally a filesystem surface. A benchmark endpoint would
change the product to accommodate the benchmark and would measure a transport
that normal agents do not use.

### Use the ordinary P3 publisher and a small harness filesystem adapter

Chosen. The self-host command publishes the latest registered P3 snapshot via
`LocalMountPublisher`. The benchmark receives the resulting P3 path and offers
the answer model the same three motions a filesystem-capable agent has: list,
grep, and read. Paths stay under the published snapshot, output is bounded, and
the `.snapshot-version` marker must equal the P3 version returned by readiness.

## Protocol identity

This is a new protocol, not a silent v10 mutation. The executable registry has
one clean current choice, `RS-LoCoMo-Full-v11` / `full-v11`; old artifacts remain
self-describing but no compatibility runner is retained.

The immutable run fingerprint includes the exact answer prompt, structured
schemas, adapter version, surface-manifest hash, and canonical hash of all 22
answer-tool descriptors. P3 tool limits and schemas are descriptor members and
therefore fingerprinted. At answer time the runner also verifies:

- the serving build revision equals the prepared repository revision;
- the deployment surface-manifest hash equals the prepared hash;
- the three live recipe descriptors equal the canonical descriptors;
- all nine open-query names match the public open-query authority;
- P2 and P3 are fresh for the exact ingested versions;
- the supplied P3 mount marker equals that readiness report's P3 version;
- the isolated deployment contains exactly the prepared session versions.

## Tool execution and failure behavior

Assured operations and direct primitives return complete D49 envelopes. Open
query returns its public JSON payloads. P3 returns bounded JSON objects that
include the snapshot version and relative path. Durable traces retain every
payload; the reader prompt removes only the same non-semantic empty containers
and rank bookkeeping already removed for envelopes.

Expected caller mistakes in an exploratory call (typed parse, argument,
allowlist, or saved-query-state errors, plus local argument/path rejection) are
recorded as failed tool results so the bounded agent can correct its plan.
Status alone is insufficient: open-query quota/schema/P2 failures can use HTTP
404/409, and SQL/Cypher failures can arrive inside HTTP-200 `QueryResult/v1`
payloads. Authentication, authorization, quota, transport, projection/store,
and server failures remain terminal for the item. The existing
eight-tool/nine-agent-call ceiling prevents an error loop and stays comparable
in cost posture.

The existing session-recall diagnostic can only attribute sessions carried by
envelope claim/chunk results. SQL, Cypher, and P3 may answer correctly without
producing those typed envelope rows, so the diagnostic must say “envelope
evidence only”; it is not a full-plane retrieval-recall metric and is not used
for the primary score.

## P3 safety and recovery

The P3 adapter resolves the published snapshot once and rejects absolute paths,
`..`, symlinks or resolved paths outside that root, non-text files, and reads
beyond the fixed byte/line caps. Listing, grep, and read never modify the tree.
The mount publisher atomically swaps the P3 symlink only after a whole snapshot
has materialized, so a reader sees one complete version.

If the mount is missing, unreadable, or version-mismatched after readiness, the
answer command checkpoints an explicit terminal failure for every remaining
question so the denominator stays complete and no partial plane is scored.
Those durable zeroes are not replaced on resume: after repairing publication,
a clean retry requires a fresh run and fresh ingestion. A fresh v11 run also
re-ingests because the revision guard intentionally rejects data processed by
the prior v10 image.

## Cost and operational consequences

The wider catalog adds prompt tokens and lets the agent make semantic SQL calls,
but the existing call and reported-spend ceilings remain the controlling cost
boundary. P3 calls are local and model-free. The publication run still uses one
isolated deployment per LoCoMo conversation and may shard those deployments
across independent VMs.

No benchmark-specific production behavior, hidden database access, extra
retrieval model, compatibility branch, or automatic paid run is introduced.

## Implementation and validation

1. Add the three missing typed primitive SDK methods.
2. Add one normal self-host mount-publication command over
   `LocalMountPublisher`.
3. Replace the executable LoCoMo protocol with v11 and fingerprint the complete
   catalog.
4. Add the bounded P3 adapter and a single dispatcher for assured, primitive,
   open-query, and P3 calls.
5. Update focused protocol/runner/SDK/self-host tests and public benchmark docs.
6. Obtain independent Codex and Grok reviews, run focused local tests, and let
   CI run the broader suite.
7. After merge, build the exact `main` revision on the benchmark VMs, publish
   mounts, freshly ingest all ten isolated conversations, and score them.
