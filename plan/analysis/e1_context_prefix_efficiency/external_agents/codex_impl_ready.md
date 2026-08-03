# Codex — D80 implementation-readiness review

**Scope:** `e1_embedding_input_policy.md` §3.2–3.3, §4.5, §5, §6–7;
`orchestration_design.md` embed-chunk durability; `e2_e3_claims_relations_design.md`
§3.1 and the D80 grounding union; `retrieval_design.md` D80 amendments.

## Verdict

**Ready to implement.**

The reviewed contracts are mutually consistent and specific enough for an implementer to
start without choosing storage identity, lexical text layout, grounding inputs, claim-filter
behavior, or embed recovery semantics.

## Previous must-fixes

1. **`passage_generation` / grain confusion — closed.** E1 §4.5 now separates:
   `embedder_generation` (deployment/model configuration), `policy_generation` (policy
   artifact), and per-chunk embedding identity. The active query/cutover pointer and the P1
   row key both use the explicit `(policy_generation, embedder_generation)` pair; the
   per-chunk `embedding_text_hash` is a verification stamp, not part of that pointer.
   Orchestration uses the same pair for batching, recovery, stamps, and readiness, while
   retrieval verifies the same pair plus hash during hydration. The remaining prose use of
   `passage_generation` is resolved locally by these explicit tuple definitions and does not
   leave a schema choice.

2. **P1 text column body vs header+body — closed.** E1 §7 binds the P1 text/BM25 column
   to normalized body only, explicitly excludes `location_header`, and keeps vectors computed
   from the policy's embedding text. Retrieval repeats that body-only rule and returns the
   optional header separately from the source body. No stripping rule or BM25-layout decision
   remains for the implementer.

## Remaining contract check

- Connector metadata has a minimum typed payload and an explicit structure-only fallback, so
  D80 does not wait on connector-specific schemas.
- E2 receives the closed `LocationElement` collection independently of embedding mode. Its
  normative grounding union agrees with E1: target body, source-derived document header,
  same-section neighbours, and typed location elements are in; free-form headers, summaries,
  model-derived orientation, labels, and ordinals are out.
- Claim P1 rows do not inherit message scalars; filtered claim recipes join through the origin
  chunk or document location facts.
- Embed work has a representation-scoped claiming row, bounded same-scope batches, deterministic
  call keys, poison isolation, P1-before-PG recovery, exact generation/hash reconciliation, and
  an active-generation readiness barrier with typed skips.

## Invent-or-block items

None.
