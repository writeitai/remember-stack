# Response to GPT-6 Sol D140 implementation review, round 1

Review: [REVIEW_gpt-6-sol_d140_impl_r1_2026-10-01.md](REVIEW_gpt-6-sol_d140_impl_r1_2026-10-01.md)
(reviewed `c57c723c`). Every finding is addressed; each fix has a test that fails on the
reviewed code. Commits: `2d82f75f`, `bcedc0b5`, `d61f5757`, `9f03ad53`, `86a33db5`,
`61143115` (+ the benchmark record).

## Deviations

| Finding | Resolution | Tests |
| --- | --- | --- |
| (a) graph gate after traversal — **rejected** | The gate moved into traversal. The graph role is granted exactly one private, deployment-bound predicate, `rememberstack_graph_internal.relation_evidence_in_scope` (`SECURITY DEFINER`, view-owner-owned; graph and query roles have `EXECUTE`). `graph_neighborhood`/`graph_path` (rebuilt from the D118 chosen-window definitions) apply it to each BFS level's ordered candidates before the level's expansion limit; the one-hop guard and PGQ statement apply it before their budget. All inside the traversal's snapshot; the separate engine connection and post-hoc filter are gone. Graph catalog repair (`rebuild_fact_graphs`) recreates the gate and the gated helpers. Design §8.1 binds this. | `test_an_ineligible_first_edge_never_takes_the_only_result_slot[1,2]` (limit=1, excluded edge sorts first), `test_traversal_and_gate_read_one_snapshot` (deletion committed after the caller's snapshot), graph suite and D140 migration catalog checks |
| (b) MCP writes — accepted | No change. | — |

## P1

| # | Resolution | Tests |
| --- | --- | --- |
| 1 vacuous gate | The gate needs an in-scope supporting occurrence; the "no live support → eligible" arm is gone everywhere (current and pinned paths, `memory_v1.fact_in_scope_support`, `claim_in_scope`). Generality: a fact with **no supporting evidence at all** (D54's zero-support, contradiction-only case, which D54 flags rather than hides) is judged by its evidence of either stance, so undated corpora read exactly as before; design §8.1 binds this. Facts with support that survives only in deleted versions or replaced readings are not eligible. | `test_a_fact_without_live_supporting_text_is_not_eligible` (scored and nominated fact channels, `facts_context`, the SQL function; includes D54 parity for a contradiction-only fact), `test_the_evidence_gate_follows_the_editions_in_force` |
| 2 replaced reading | Both gate predicates, `claim_in_scope` and the SQL function require the occurrence's chunk to lie in its version's current representation. | `test_an_occurrence_left_in_a_replaced_reading_does_not_support_a_fact` |
| 3 pre-commit stamps | Every effective-time write holds a deployment belief guard (shared advisory lock) from its stamp to commit; the first page of `search_documents`, `document_references` and `section_history` takes it exclusively on its own connection (session lock, then ends that transaction), reads the database clock and only then opens its snapshot. Reads without a pinned belief use every committed declaration. Design §3.6 binds this. | `test_a_first_page_never_misses_a_correction_stamped_before_its_belief` (two sessions; writer held between stamp and commit; fails without the guard) |
| 4 chunk handles | `document_references` accepts a chunk only from a ready version's current ready representation. | `test_a_chunk_handle_must_lie_in_a_ready_versions_current_reading` (processing version; obsolete chunk after a reading swap) |
| 5 fact evidence via origin | A private view `v_memory_claim_carried_periodised` (current claims of periodised lineages whose origin version is gone but a live version still carries them in its current reading) is unioned into the two private fact-authority views, so fact visibility, D54 counts and `facts_context` evidence all keep such claims; hydration reads the claim's immutable fields from the base table and shows the selected occurrence. | `test_facts_context_shows_a_reused_claim_after_its_origin_version_is_deleted` |
| 6 pending on empty answers | When some lineage has an in-force version that is not ready for the window (partial GiST index `ix_version_scope_pending`), one bounded lexical probe over just those lineages' readable editions names the ones the query's terms reach (positive BM25 scores only; no second embedding; never enters the answer). Wired into `search_chunks`, `search_claims`, `claims_and_sources_context` and ranked `search_documents`. | `test_an_empty_answer_names_the_pending_lineage_the_query_reaches` (unfiltered; an unrelated pending lineage is not named) |

## P2

| # | Resolution | Tests |
| --- | --- | --- |
| 1 walk key and tombstones | The filter-only walk key ignores later tombstones; liveness is applied when a candidate is judged. | existing paging tests; walk key covered by `test_paging_pins_belief_across_a_retroactive_correction` |
| 2 pinned pending | A belief-pinned page derives pending lineages from the intervals as known at the pinned instant plus current readiness (`_SCOPE_PENDING_AT`). | paging suite |
| 3 downgrade guard | The downgrade also refuses when section keys/hashes or chunk `text_origin_at`/`reuse_identity_hash` exist. | `test_the_downgrade_refuses_when_d140_data_exists` (four new cases) |
| 4 scale measurement | See **Benchmark** below. | — |

## Other changes made while fixing

- Ranked chunk statements now **join** `document_version_scope` instead of an `EXISTS` probe
  per chunk: the benchmark showed join-first plans walking every version's chunks.
- Test fixtures that inserted claims without the origin `chunk_claims` row (or ready versions
  without `current_representation_id`, or claims of documents that do not exist) now write
  what production always writes; the gate correctly no longer accepts the unrealistic shape.
- Owner decision (2026-10-02): the one-time full re-extraction after upgrade is accepted; the
  plan states it plainly, with no backfill or workaround.

## Benchmark

_Filled in from the VM run (see plan "Verification")._
