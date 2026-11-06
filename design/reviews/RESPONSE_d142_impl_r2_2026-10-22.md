# Response — D142 implementation review, round 2 (GPT-6.1 Sol)

Review: `REVIEW_gpt-6.1-sol_d142_impl_r2_2026-10-22.md` (on `0ad5a0be9`).
Scope set by the coordinator: fix P1-1 to P1-5, every P2 and the nits;
**P1-6 (the latency gate) waits for the owner** and stays open. `origin/main`
is merged, so CLAUDE.md Rule 4 and the `AGENTS.md` pointer are in the branch.
Rule 4 holds: nothing here changes chunking, reuse keys or the Selection,
Claimify or embedding inputs.

Postgres proofs ran on an isolated, short-lived VM (`d142-r2`) using the
branch's built engine image: the full integration suite passed (1,231 passed,
2 skipped, 1 expected failure). The VM was deleted afterwards (no instances
or disks remain in the project).

## P1

1. **One authority instant in the sandbox.** Bridge fact nomination no
   longer overrides the imported instant (`semantic_facts` passes no
   `evaluated_at`). Fact confirmation applies `facts_current`'s own
   predicates at the request transaction's `now()` — the exported instant —
   instead of the view's `statement_timestamp()`, and reports that instant
   as `pg_confirmed_at`. User SQL that reads `memory_v1.facts_current`
   directly keeps that view's per-statement contract; the bridge's rows are
   the request's. Proof: two fact SRFs over a fact whose declared validity
   ends while the request nominates both keep it, at one reported instant.
2. **The request and operation deadlines bound everything.** Fact and entity
   entry points take `min(caller, operation)` deadlines; the loop clamps
   `statement_timeout` to the time left before attestation, every FETCH and
   every confirmation; the sandbox clamps before bridge confirmation and
   before the outer statement; standalone `facts_context` uses the
   operation's deadline. Proofs: fact and entity searches inherit the
   operation deadline; SRFs that spend the request budget leave the outer
   work a timeout (`statement_timeout`).
3. **Typed timeouts, original errors kept.** SQLSTATE 25P04 counts as a
   deadline like 57014; `nominate_claims`/`nominate_chunks` and standalone
   `facts_context` answer a typed `p1_deadline` boundary; planner settings
   are restored only in a healthy transaction, so a cancelled EXPLAIN or
   DECLARE keeps its error (a rejected plan still restores). Proofs: real
   statement timeouts during attestation and during confirmation answer
   `p1_deadline` through `search_chunks`.
4. **Accounts at resolve and the compound.** `resolve` runs in one
   operation; its T3 search keeps its account — a capped empty search is a
   boundary with its reason, a partial one carries the reason — and its
   confirmation and context reads use the operation snapshot.
   `default_facts_context` keeps the fact search's stop reason. Failing-first
   unit test for resolve.
5. **Survivor-expanded budget.** The scoped fact-scan budget counts the
   stored endpoints of every entity the requested survivors absorbed — the
   same scope `facts_visible_history` scores — reading at most budget + 1
   rows across both tables. Proof: a hub whose facts store an absorbed
   entity's id is `entity_scope_too_broad`, directly and through
   predicate-filtered `facts_context`.
6. **Latency gate — open, for the owner.** Not started, by instruction.
   The reviewer recommends the immutable `doc_id` on search rows (lineage
   identity, not visibility) plus a measured fix or an owner amendment for
   unfiltered p95; the PR body keeps the gate disclosed as failed.

## P2

1. **Margin proof.** `n` is now the parser's own count (`ts_debug` under
   `simple`, every token, compound tokens and their parts) taken before the
   loop; the derivation proves both rounding directions with explicit
   denominators (`1+γ ≤ 1+ε` and `1/(1−γ) ≤ 1+ε`), which hold exactly for
   `m·u ≤ 1/4`; longer queries are refused. Domain tests at the boundary and
   a Postgres parser test (`a-b` → 3).
2. **LoCoMo loader.** The runner gained `load_recorded_run`: every
   self-consistency check of `_load_run` (recomputed fingerprint, hashes,
   rendered documents, state/answer/judge identity) without the current
   pins. The gate requires an answer and a judge per question. The old-pin
   fixture is now prepared under that pin; missing-answer, mismatched judge
   id and edited-fingerprint runs are refused.
3. **One work budget across tiers**, with the loop and HNSW limits reported;
   unit test with two rejected large tiers.
4. **Sweep.** Public results now hydrate (the synthetic corpus has no
   embedding generation, so the harness lifts the generation pin);
   per-query accounts are recorded; the plan proxy uses the production LIMIT
   without ANALYZE. Analysis §8.1 now says latency grew sublinearly, not
   that it is corpus-independent; §8.2 is labelled diagnostic. Rerun:
   at 300k chunks with the final configuration, no stratum stops early and public results hydrate; unfiltered p95 is 263–267 ms (within the budget at that size), filtered strata are 351 ms to 2.1 s, and the exact tier is slower than the pushdown on either side of its threshold (analysis §8.3; input to the open P1-6).
5. **Owner-invariant proof.** Every Selection and Claimify call is traced to
   its target chunk: one call per new identity, Claimify reruns bounded to
   the known eight-chunk window, reuse identity and text origin carried,
   and claim reuse required for every unchanged Claimify identity. The
   expected failure holds only the requirement-vs-gap assertion. D142 states
   exactly that.
6. **Vectors as stored.** Validation runs on the float4 values pgvector
   stores (underflow to zero, overflow rejected), and zero-norm legacy
   vectors are excluded from every exact semantic path, as HNSW cannot reach
   them.
7. **Entity-scoped facts.** Design §8.5 and D142 now adopt the bounded exact
   scan as a post-approval alternative: survivor-expanded stored-endpoint
   budget counted first, typed boundary above it, exact scoring below it,
   not the tier loop and not a 5,000-row tier; its cost at 50,000 rows is
   not yet measured.

## Nits

All fixed: plan and analysis no longer list hydration and the spill-mixed
test as gaps; test docstrings say what they test (the planner test is
repeated streamed executions, not auto-prepared plans; unscoped fact
confirmation reads authority); design §8.5 states `ef_search` 200 with its
evidence; the PR body uses the fourth-power margin; D142 names the vector
validation change.
