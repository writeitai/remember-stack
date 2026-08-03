# Codex binding review — D80 / E1 embedding-input policy

## 1. Verdict

**Accept with required amendments.**

The architectural decision is sound: D80 cleanly chooses conventional interchangeable embedders, separates location facts from policy and embedding text, removes the default per-chunk location LLM, keeps summaries out of embedding and grounding, and moves provider work to bounded durable units. Most first-review must-fixes are present in the new normative E1 design.

It is not yet an implementation-complete binding contract. Four issues are load-bearing: the purported pure policy reads undeclared mutable retrieval capability; the Slack/message fallback makes the provisional eval gate ineffective; policy-version migration contradicts the vector-reuse rule and lacks dual-generation spine state; and the connector/E2/P1/Postgres wire contracts remain either incomplete or still describe the superseded LLM-prefix system. These are amendments, not reasons to reject D80.

## 2. Prior must-fix checklist

| Prior item | Status | Binding evidence and assessment |
|---|---|---|
| H9 typed groundable location, not bare header removal | **Landed** | The normative matrix admits only typed allowlisted location elements with acceptable provenance and excludes the rendered header blob and model-derived orientation (`plan/designs/e1_embedding_input_policy.md:107-122`). E2 carries the matching D80 amendment (`plan/designs/e2_e3_claims_relations_design.md:150-157`). The semantic fix landed; its final wire/schema form still needs closure under §4.4 below. |
| Connector metadata contract called out | **Partial** | The prerequisite, ownership, stable-ref rule, E0/E1 threading, shape owner, and D74 purge are explicit (`plan/designs/e1_embedding_input_policy.md:88-105`; `decisions.md:3059-3060`). What is missing is the actual typed payload, cardinality, per-message/span mapping for exports, canonical display/grounding forms, and authoritative storage. This is more than an implementation detail because a multi-message chunk cannot truthfully have one unexplained `author_ref` or timestamp. |
| Model-independent policy length counter | **Landed** | Counter identity is policy-owned and explicitly cannot be the active embedder tokenizer (`plan/designs/e1_embedding_input_policy.md:128-142`); the interchangeability checklist repeats the constraint (`plan/designs/e1_embedding_input_policy.md:316-324`). |
| D56 vector reuse = text hash + policy + embedder generation | **Landed** | The three-part rule is normative in the policy (`plan/designs/e1_embedding_input_policy.md:205-219`), E1 reuse (`plan/designs/e1_chunks_design.md:334-337`), and D80 (`decisions.md:3052-3055`). The policy-change row in the migration table conflicts with it, addressed in §4.5. |
| Work graph at failure boundaries, not pure-function ledger spam | **Landed** | Pure prepare may commit stamps in a representation transaction without a row per function, while chunk embed work is durable and coalesced into provider batches (`plan/designs/e1_embedding_input_policy.md:258-286`). The remaining batch ownership/recovery details are narrower amendments, not a reversal of the decision. |
| Storage D37: no full body in Postgres | **Landed** | PG stores typed facts/stamps, bounded header, hashes, refs, and source offsets; full embedding text remains in P1 (`plan/designs/e1_embedding_input_policy.md:296-312`; `plan/designs/e1_chunks_design.md:341-349`). |
| Slack `body_only` provisional plus eval gate | **Partial** | The text labels the short-atom default provisional and requires filtered and unfiltered evaluation (`plan/designs/e1_embedding_input_policy.md:159-164,345-356`). However, a `message_atom` that fails rule 2 normally reaches rule 6 and is still `body_only`; the promised compact-header fallback is not encoded. Thus the gate does not currently govern the outcome. |
| No global `i/N` in default headers | **Landed** | Stable anchors are preferred (`plan/designs/e1_embedding_input_policy.md:78-79`), global ordinals are excluded (`plan/designs/e1_embedding_input_policy.md:181-192`), and they are an explicit non-goal (`plan/designs/e1_embedding_input_policy.md:362-370`). |
| Design home under E1 / D80 | **Landed** | The new document identifies itself as binding E1 input semantics and a child of E1 §5 (`plan/designs/e1_embedding_input_policy.md:8-16`); E1 §5 summarizes and delegates to it (`plan/designs/e1_chunks_design.md:260-285`), and D80 binds the change (`decisions.md:3028-3078`). The amendment blast radius is only partially reconciled, addressed under H8. |

## 3. High-level decisions H1–H9

| ID | Residual decision | Review |
|---|---|---|
| **H1** | **Accept** | Conventional `texts -> vectors` plus version-scoped migration is now explicit. Interchangeability covers model, dimension, metric, provider caps, and query/document preprocessing (`plan/designs/e1_embedding_input_policy.md:24-34`). D63 is explicitly amended without rewriting history (`decisions.md:2044-2049`). |
| **H2** | **Accept with amendment** | Facts, policy, exact embedding text, and display body are separated (`plan/designs/e1_embedding_input_policy.md:42-58`). Amend the function/input state and P1 columns so `document_stats`, source body, lexical text, optional header, and vector input do not become implicit overloads. |
| **H3** | **Accept** | No model call exists on the default location/render path, provenance admits upstream model assistance honestly, and an LLM location variant is a non-goal until separately designed (`plan/designs/e1_embedding_input_policy.md:35-38,82-86,288-292`). |
| **H4** | **Amend** | Conditional headers are correct, but the written procedure is not yet the claimed total pure function: it reads undeclared filter capability, its signature disagrees across sections, and compact/full selection still uses non-executable predicates. |
| **H5** | **Amend before freezing v1** | Shape-aware behavior and the eval plan are right. The actual rule must make compact header the fallback for a located atom when `body_only` is not eval-approved or usable; the final `else -> body_only` currently defeats that policy. |
| **H6** | **Accept** | D79 summaries are orientation-only, not default embedding input, and not grounding sources across D79, D80, E1, and E2 (`decisions.md:2931-2938,3022-3026`; `plan/designs/e1_embedding_input_policy.md:84-86`). |
| **H7** | **Accept with amendment** | The hybrid prepare/chunk-work/batch-provider/barrier shape is correct. Bind the exact durable work identity, batch cost owner, split-call keys, post-P1/pre-PG recovery, and readiness semantics. |
| **H8** | **Accept with required reconciliation** | The normative home and main cross-links are good. Required binding surfaces are still stale: E2's base bundle still requires an E1 prefix, and the Postgres schema still defines an LLM `context_prefix`/`prefixer_version` rather than D80 state. |
| **H9** | **Accept with required contract closure** | The decision itself is now correct. Close the typed element schema and update `added_context` provenance so the implementation cannot fall back to the legacy `header|neighbour|prefix|hint` vocabulary. |

No H1–H9 decision warrants rejection. H4, H5, H7, H8, and H9 need concrete amendments before this is a reliable implementation contract.

## 4. Mechanism deep-dives

### 4.1 Embedding-input policy

The artifact boundary is strong. It versions normalization, escaping, counter identity, allowlist/order, null rules, constants, and precedence (`plan/designs/e1_embedding_input_policy.md:128-142`). Pinning title metadata to a version or a rerender event and excluding volatile ordinals are also correct (`plan/designs/e1_embedding_input_policy.md:181-192`).

The policy is nevertheless not yet total or pure as written:

1. Section 2 defines `(location_facts, body) -> ...`, while §4.3 adds `document_stats` (`plan/designs/e1_embedding_input_policy.md:47-48,154-157`). `chunk_count` affects mode, but neither the location snapshot nor the migration table identifies a `document_stats` snapshot/version or a rerender trigger when it changes.
2. Rule 2 reads whether “useful message scalars are projected for filters” (`plan/designs/e1_embedding_input_policy.md:159-164`). That is mutable retrieval/deployment capability, not one of the declared inputs. A recipe or projection-config change could therefore change embedding text without a policy bump, while the migration table currently labels scalar-only changes no-reembed (`plan/designs/e1_embedding_input_policy.md:194-203`).
3. “Useful coordinates,” “real section title,” “discriminative source_shape,” and “full or compact per body length” are design intent, not executable predicates (`plan/designs/e1_embedding_input_policy.md:165-179`). The policy artifact may own their exact definitions, but the binding contract should say so and define its schema, including exact compact/full fallback and field truncation behavior.
4. The Slack gate has a control-flow bug. A short `message_atom` gets rule-2 `body_only`; a located atom for which rule 2 is false generally misses rules 4 and 5 and gets rule-6 `body_only` anyway. Encode a message-atom branch explicitly: use `body_only` only when an eval-approved policy flag and required filter capability snapshot permit it; otherwise use a compact header when ground-truth coordinates exist, and use body-only only as a disclosed no-coordinate degradation.
5. Oversized atomic blocks are permitted by E1 (`plan/designs/e1_chunks_design.md:202-204`), while D80 only defines an empty-body skip. Interchangeability needs a provider-capability preflight and a typed oversize outcome (or a product-wide maximum input guarantee) so a lower-context conventional embedder does not turn a migration into undefined provider failures.

Required shape: make the pure input something like `(location_facts_snapshot, body_bytes, policy_eval_context_snapshot)`, with normalization still owned by the policy artifact and the last object containing only policy-versioned deterministic facts such as chunk-count class and an immutable filter-capability flag. Every field capable of changing mode/header must be listed as a rerender trigger.

### 4.2 P1 scalars, text columns, and hydration

The scalar discipline is good: a small universal set, stable opaque source-specific refs only when recipes support operators, Postgres authority for display values, and D74 purge (`plan/designs/e1_embedding_input_policy.md:223-247`). Retrieval correctly requires scalar prefilters and generation-safe search (`plan/designs/retrieval_design.md:157-161`).

Three contracts remain open:

1. **No recipe actually closes H5's dependency.** “Only when recipes declare operators” is a rule, not a declared operator set. Name the v1 filters and targets—at least whether `channel_ref`, `thread_ref`, `author_ref`, and time-range apply to chunk semantic, chunk BM25, and claims—and define behavior when an index/capability is absent. A policy must not query live recipe availability; it must consume a frozen capability generation or remain independent of it.
2. **P1 generation is conflated with embedder generation.** The universal scalar list contains only active embedder generation (`plan/designs/e1_embedding_input_policy.md:230-235`), but hydration verifies policy version + text hash + embedder generation (`plan/designs/retrieval_design.md:148-153`). A policy-only migration can therefore mix old/new representations under one embedder generation. Define a passage-representation or P1 projection generation whose identity includes at least policy version and embedder generation, and make the query pointer/cutover use that identity.
3. **The text-column contract is ambiguous.** E1 says display body and embedding text differ and that full embedding text lives in P1 (`plan/designs/e1_embedding_input_policy.md:50-53,296-308`). Retrieval says semantic and BM25 use the same P1 table's text column, then says P1 supplies source body and an optional header separately (`plan/designs/retrieval_design.md:128-153`). Bind the actual row: source/display body, bounded header, exact vector-input bytes or their reconstructable equivalent, hash, vector, and composite generation. BM25 should explicitly index the intended body/header field rather than inherit whichever string happened to be embedded.

The unresolved claim-scalar inheritance choice (`plan/designs/e1_embedding_input_policy.md:249-254`) should be decided in retrieval design before message filters are advertised on claims; until then capability discovery must say those filters are chunk-only.

### 4.3 Work graph and failure boundaries

The chosen graph addresses the original scalability failure: deterministic prepare can commit in one representation transaction; provider work is durable per chunk but batched; P1 precedes PG stamping; and a readiness barrier prevents mixed incomplete representations (`plan/designs/e1_embedding_input_policy.md:258-286`). Orchestration agrees that E1 fans out at chunk grain (`plan/designs/orchestration_design.md:59-62`).

Implementation still cannot derive a unique state machine from the documents:

- `processing_state` is one row per target/stage/component version, and cost rows are unique only under `(processing_id, attempt, call_key)` (`plan/designs/orchestration_design.md:75-81,137-153`). Bind `embed_chunk`'s component version as a composite representation version, not merely the model generation; otherwise a policy-only rerender is not new work identity.
- State whether a provider batch may cross documents. Existing orchestration says a billed batch never crosses a document or lane and belongs to one claiming processing row (`plan/designs/orchestration_design.md:140-143`), while D80 only says chunk work is coalesced. If multiple chunk rows share one call, define the owner, deterministic original/split call keys, and how successful sibling chunks become durable without marking failed siblings complete.
- “Upsert P1, then stamp PG; retries are idempotent” needs the recovery rule for a crash between those writes (`plan/designs/e1_embedding_input_policy.md:276-283`). On retry, the worker should recognize an exact composite-key P1 row and stamp PG without paying the provider again. Conversely, a mismatched row must never be accepted just because `chunk_id` matches.
- Define the readiness predicate over versioned embedding records and closed typed skip codes. Empty normalized bodies may be a legitimate skip; provider poison, missing metadata, or oversize must not silently count as ready unless the product explicitly accepts the resulting retrieval hole.

These additions preserve the desired failure boundaries; they do not require ledger rows for the pure resolver/renderer.

### 4.4 E2 grounding and connector/message metadata

The D80 grounding rule is correct: typed source/connector/deterministic elements enter the union, while free-form header, model orientation, summaries, mode labels, and ordinals do not (`plan/designs/e1_embedding_input_policy.md:107-122`). It also correctly makes grounding independent of whether the passage embedding used `body_only`.

The final E2 bundle is not yet coherent. Its base contract still lists the E1 context prefix, describes per-document prefix caching, defines `added_context` tags as neighbour/header/prefix, includes the stored prefix in the union, and uses the prefix in the worked example and open spike (`plan/designs/e2_e3_claims_relations_design.md:48-62,105-113,138-157,332-363`). The later D80 amendment semantically supersedes those clauses, but an implementer still has two incompatible schemas to choose from.

Rewrite the effective contract—not just append another note—to say that E2 always receives a typed location-element collection independent of the optional embedding header. Define a closed element record, for example: stable element id, kind, canonical groundable text, raw/source locator or connector field ref, provenance, and schema version. Update `added_context.source_kind/source_ref` to point at such elements (with advisory attribution preserved) and retain the token-tolerant union check.

The connector portion needs the same precision. `source_shape` at document/version grain is insufficient for `thread` or `channel_export`: a chunk may cover several messages, authors, and timestamps. Bind whether connector metadata rides document, message/block source-map spans, or both; define plural-author behavior and time ranges; and specify canonical display text separately from stable opaque refs. The connector must never collapse a multi-author chunk to one author merely to fill a scalar/header. Then bind storage and D74 removal for both the authoritative metadata and every P1 generation.

This incompleteness is visible in another binding document: `postgres_schema_design.md` still describes an LLM-derived `context_prefix`, `prefixer_version`, old `added_context` kinds, and no D80 hashes/facts/generation state (`plan/designs/postgres_schema_design.md:1258-1295,1338-1380`). D80's new E1 storage prose is correct but does not by itself reconcile that schema.

### 4.5 Reuse and migrations

The migration table usefully distinguishes body, location, policy, scalar-only, embedder, and summary changes (`plan/designs/e1_embedding_input_policy.md:194-203`). Version-pinned title metadata and generation-safe P1 cutover are also the right direction.

There is a direct contradiction: a policy-version change says “embed iff `embedding_text_hash` changes,” but vector reuse immediately below requires policy version to match (`plan/designs/e1_embedding_input_policy.md:194-212`). If policy v2 renders byte-identical text under the same embedder, the table says no embed while the reuse key forbids carrying the v1 vector. Bind one of two valid choices:

- conservatively re-embed on every policy bump; or
- permit a zero-provider-call vector copy/attestation into a new composite P1 generation when exact bytes/hash and embedder generation match, recording the new policy identity and provenance.

Do not leave a single row stamped with old vector identity and new policy identity.

Atomic P1 cutover also requires **dual generation state in the spine**. If each chunk's only PG stamps are overwritten as v2 completes while queries still target v1, hydration will reject valid v1 rows during the backfill. If PG stamps remain v1 until cutover, v2 cannot be verified without an atomic corpus-wide PG rewrite. Bind versioned per-chunk embedding records (or an equivalent generation manifest) plus a deployment/query active-generation pointer. Cutover changes the pointer after all required chunk records exist; old state remains available until retirement. The active identity must include policy and embedder generation, not only the latter.

The migration matrix should also name: `document_stats` threshold changes; filter-capability generation changes if policy is allowed to depend on them; connector metadata backfill/correction; `source_shape` correction; and D74 during an in-flight dual-generation migration. For each, specify rerender, scalar projection, vector call/copy, readiness, and cutover effects.

## 5. Gaps that still block implementation

1. **A closed, genuinely pure policy input and executable decision tree.** The undeclared filter-capability read, missing `document_stats` snapshot/trigger, and message-atom fallthrough prevent deterministic implementation and invalidate the Slack eval gate.
2. **A coherent representation-generation and migration model.** Resolve the policy-change/reuse contradiction; separate P1/query representation generation from embedder generation; and bind dual-generation PG/P1 state plus atomic pointer cutover.
3. **The connector-to-chunk location contract.** Define typed payloads, stable and display forms, span/cardinality semantics for thread/channel exports, storage, provenance, and purge.
4. **One final E2 grounding wire contract.** Replace the still-normative prefix bundle/provenance vocabulary with typed location elements and align the claims schema.
5. **Concrete P1 and Postgres schema amendments.** Define body/header/vector-input columns and filter indexes; replace the old LLM prefix columns with D80 snapshot/hash/policy/generation records without duplicating full bodies in PG.
6. **Durable batch identity and recovery.** Bind processing component identity, document/lane batch bounds, cost ownership/split keys, post-P1 crash recovery, and readiness/skip outcomes.

These gaps block implementation, not the architectural choice. None requires reviving the per-chunk LLM or adopting contextual embedders.

## 6. Ranked disposition

### Must fix before merge

1. Repair policy purity and totality: freeze all inputs/capabilities, add all rerender triggers, and encode an effective Slack compact-header fallback.
2. Bind composite passage-representation generations, resolve policy-only vector reuse, and specify dual-generation PG/P1 cutover state.
3. Close the connector/message schema and final E2 typed-location/`added_context` contract, including multi-message chunk cardinality.
4. Amend the binding Postgres/P1 contracts so they no longer describe the superseded LLM prefix and so lexical/body/header/vector-input behavior is unambiguous.
5. Specify durable chunk-work/batch ownership, split call keys, cross-store retry recovery, and readiness outcomes.

### Should fix

1. Bind the provider-cap preflight and typed oversize behavior for E1's allowed oversized blocks.
2. Decide claim-row scalar inheritance before advertising message filters on claims; otherwise declare those filters chunk-only.
3. Add migration rows for stats/capability changes, connector corrections/backfills, shape corrections, and D74 across dual generations.
4. Clean the non-binding worker inventory and examples that still say the D63 LLM `context_prefix` worker exists; this is analysis drift, not competing binding authority (`plan/analysis/workers.md:79-88,158-177`).

### Fine as written

- Conventional-only interchangeable embedders and explicit retirement of the contextual product branch.
- The location-facts / policy / embedding-text / display-body separation as an architectural boundary.
- No default location LLM and no undesigned escape hatch.
- Summaries as orientation only, excluded from embedding input and grounding.
- Policy-owned length counter, conditional-header principle, no global `i/N`, and D37 storage discipline.
- Failure-boundary placement: pure prepare without ledger spam, durable provider work, representation barrier.

## 7. Executive summary

- **Verdict: Accept with required amendments.**
- D80's architecture is correct and should remain binding.
- H1, H3, and H6 are cleanly resolved; no H1–H9 decision requires rejection.
- Typed E2 location, model-independent counting, three-part vector identity, D37 storage, and failure-boundary placement largely landed.
- The Slack `body_only` eval gate is ineffective because the final fallthrough selects `body_only` anyway.
- The policy is not pure while it reads undeclared live filter capability and unversioned document stats.
- Policy-only migration contradicts the reuse key and lacks dual-generation spine state.
- Connector metadata needs a real typed, span-aware contract for multi-message chunks.
- E2, P1, and Postgres still expose incompatible legacy-prefix versus D80 contracts.
- Fix those contracts before merge; no fix should reintroduce the per-chunk location LLM.
