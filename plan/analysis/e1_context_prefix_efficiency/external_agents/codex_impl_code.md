# Codex — D80 implementation review after fixes

**Verdict: Request changes**

The whole-field renderer, per-batch P1→PG ordering, E2 generation bump, and
body-only happy path improved. The following remaining issues still require
inventing or violate already-bound D80 contracts.

## Must-fix findings

1. **P1→PG recovery and generation cutover are still not implemented.**
   `P1ChunkRow` carries neither `policy_generation`, `embedder_generation`, nor
   `embedding_text_hash` (`model/chunks.py:166`), and Lance still replaces rows
   solely by `chunk_id` (`adapters/selfhost/lance.py:68-88`). If the process
   crashes after `_commit_batch` writes P1 but before PG (`workers/e1.py:360-363`),
   retry cannot recognize that P1 row and calls the provider again. Conversely,
   an exact PG stamp whose P1 row is missing is excluded from `need_embed`; a
   failed `chunk_vectors` lookup is silently followed by E2
   (`workers/e1.py:229-259,309`). Searches also have no active-generation filter.
   Implement the bound composite P1 key/hash lookup and active-generation
   pointer; ordering alone does not provide crash recovery or safe cutover.

2. **The durable batch contract remains incomplete.** The provider call is a
   single unguarded call per batch (`workers/e1.py:284-308`): there is no poison
   split, single-chunk typed failure/skip, or readiness barrier. Prepare stamps
   are written only after a successful vector, empty-body skips are never
   persisted, and the handler always enqueues E2. A poison chunk can therefore
   block the document, while missing/mixed rows can advance as embed-ready.

3. **The production location/grounding path is not the typed D80 path.** E1
   hard-codes every source to `source_shape="document"` and supplies no connector
   refs (`workers/e1.py:366-387`), making the implemented message/thread policy
   unreachable outside unit tests. E2 then trusts arbitrary `kind`/`text` JSON
   without validating the closed `LocationElement` enum or allowed provenance,
   and its legacy fallback adds non-allowlisted `section_role` to the grounding
   union (`workers/e2.py:731-761`). This can admit invented/model-derived text as
   source grounding. Persist/map the minimum connector facts and validate the
   typed element records before union membership; never promote legacy free-form
   data.

4. **Legacy retrieval is silently mis-hydrated.** The migration only adds
   nullable columns and does not backfill/rebuild legacy P1 rows
   (`p8_01_0021_d80_embedding_input.py:21-39`). Query confirmation now treats
   every P1 projection as body-only while falling back to legacy
   `context_prefix` (`surfaces/query_engine.py:1554-1565`). Existing rows whose
   P1 text is `prefix + "\n\n" + body` are returned with that prefix embedded in
   `chunk_text`, and no generation/hash confirmation distinguishes them. Add a
   migration-safe legacy branch or require/rebuild the D80 generation before it
   is queryable.

5. **The test suite is red.** `uv run pytest --collect-only -q` fails because
   `test_d79_summary_consumption.py` still imports removed `_prefix_prompt`.
   The focused D80/migration run also fails
   `test_revision_graph_is_one_linear_structural_chain` because `p8_01_0021` is
   absent from the expected chain. Add the missing updates plus fault-injection
   coverage for P1→PG recovery, missing-P1 repair, poison split/readiness, typed
   E2 validation, and legacy query hydration.
