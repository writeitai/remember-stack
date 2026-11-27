# Response to the scoped D163 design review, round 4

Review of PR #682 at `e1b37d320`. I accepted the P1 and P2 findings.

## P1

- **Retained-store restore.** Immediate foreign keys form cycles between
  documents and versions and between versions and readings. The oracle
  specifies a staged restore with PostgreSQL 18 `pre-data` DDL, the original
  rows and vectors, then `post-data` DDL. It requires matching pointers,
  row counts and key-ordered row and vector hashes before upgrade and cloning.
  A reduced PostgreSQL 18.6 fixture reproduced `23503` on direct loads.
  The staged restore preserved pointer and vector rows, validated the foreign
  keys and built the HNSW index.
  [Document/version cycle](../../src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L384-L396) ·
  [Version/reading cycle](../../src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L443-L452) ·
  [Qualification](../../plan/designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification)

## P2

- **P1 fact-search contract.** The deployed batch confirmation calls
  `fact_eligibility_ctes`, and the public wrapper applies the same subject
  and relation-object visibility checks. P1 §§8.3 and 8.5 now call for D163's
  candidate-bound lookups in native confirmation and set-based source joins
  in broad SQL, with the same fact visibility predicates in both paths.
  [Native batch confirmation](../../src/rememberstack/adapters/postgres_p1.py#L1664-L1679) ·
  [Public authority wrapper](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L398-L408) ·
  [P1 batch confirmation](../../plan/designs/postgres_p1_search_projection_design.md#83-nominate-confirm-continue-d142) ·
  [P1 fact search](../../plan/designs/postgres_p1_search_projection_design.md#85-facts)

Full-schema equivalence, migration and latency remain implementation gates.
