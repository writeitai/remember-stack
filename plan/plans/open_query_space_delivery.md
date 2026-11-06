# Open query space — delivery order

Build order for [the open query space design](../designs/open_query_space_design.md).
Moved here from the design's former §11 (2026-10-21, D142 review) so that the
design states only architecture; the batches below are unchanged in content.
Section references (§…) point into the design.

1. **Batch A — schema contract:** machine-readable manifest, full DDL note,
   invariant views, comments, canonicalizer/hash, D41/D48/D54 tests.
2. **Batch B — safe execution:** roles/grants tenancy, parser/allowlists, limits,
   QueryResult, discovery, audit/cost controls, adversarial suite.
3. **Batch C — PostgreSQL-native P1 operations:** `semantic_claims`,
   `semantic_chunks`, `semantic_facts`, `semantic_entities`,
   `lexical_claims`, `lexical_chunks`, and `fetch_chunk_bodies`; shared P1-port
   paths, D80 current-attestation readiness, snapshot-shared authority confirmation (D142), body
   verification, rank/score and drop disclosure, caps, cancellation/telemetry,
   manifest signatures, and per-surface OSS pages. `lexical_facts` remains only
   the §10 recorded alternative.
4. **Batch D — live graph:** deployment-keyed invariant vertex/edge views,
   exact PostgreSQL 19 property-graph DDL and grants, semantic-catalog readiness,
   server-owned fixed one-hop SQL/PGQ templates, and level-at-a-time bounded
   recursive helpers. It includes endpoint indexes; depth/frontier/examined-edge/
   result/temp/time budgets; deterministic shortest-hop behavior; explicit
   repeated-element exclusion where required; truncation telemetry; and the
   PGQ/recursive parity, tenancy, catalog, privilege, fault, and cap proofs in
   §9.1–§9.10. P1/PostgreSQL remain the authorities inside
   `claims_and_sources_context` and `facts_context`.
5. **Batch E — customer space:** immutable registry, governance, all 18
   `examples.*` mappings (17 demoted plus citation path), drift and deletion behavior.
6. **Batch F — integrated surface:** API/SDK/CLI/MCP additions, exactly four
   assured operation descriptors, consumption-skill/docs rewrite led by the
   verbatim two-layer headline and all four bound facts-layer examples. The 17
   demoted patterns ship only as `examples.*`; no automatic paid run or
   deprecation telemetry is part of delivery.

A later batch cannot merge around a failed dependency gate. Each behavioral
change ships with same-change OSS documentation, manifest/version updates, and
the relevant §9 evidence. Batch F integrates and leads with the rewrite; it
does not substitute for the per-surface documentation in earlier batches.

Batch D depends on Batches A and B and has no Batch C dependency. Assured context
operations depend on Batch C because they reuse the shared PostgreSQL D48/D41
authority and P1 nomination/body machinery; they cannot merge around a failed
Batch C confirmation gate.
