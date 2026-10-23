# LoCoMo protocol history through v38

Historical, non-binding record moved from the design on 2026-10-23.
These are superseded evaluation identities, not runnable alternatives.
Git and saved run files retain their exact original implementations and data.


> **v38 retrieval adjacent-chunks amendment (2026-09-21).** Direct retrieval
> primitives expand from 7 to 8 with `adjacent_chunks` (symmetric window
> `1 <= window <= 2`), bringing the complete benchmark answer catalog from 21
> to 22 tools. Tool catalog SHA-256 and protocol fingerprints roll accordingly.
> Pipeline stages, component generations, prompts, budgets, models, live graph,
> dataset, and judge remain identical to Full-v38; stores ingested under v38
> remain fully compatible and do not need re-ingestion. Existing in-flight v38
> runs updating answers/judges must update their protocol fingerprint and catalog
> hash pins to match the 22-tool catalog. Decision: [D130](../../decisions.md).
>
> **v38 GLM ingest variant (2026-09-17).** `full-v38-glm`
> (`RS-LoCoMo-Full-v38-GLM`) replays the R14 generation model
> (`z-ai/glm-5.3-flash` via OpenRouter, reasoning effort `minimal`) under the
> full v38 pipeline: same stages including `ground_claims`, component
> generations, prompts, tool catalog, budgets, Qwen embeddings, and the frozen
> Luna answer agent and judge. Ingest model bindings move from a module-global
> constant to a per-protocol pin (`LoCoMoProtocol.ingest_model_bindings`,
> canonical map as default); the runner checks readiness and pre-upload
> bindings against the prepared protocol, so a wrong-model deployment fails
> closed naming the expected protocol. Fingerprints are untouched, so existing
> v38 run dirs keep validating. Scores are a new ingest-model family baseline,
> comparable only to other GLM-ingest runs, never to Luna-ingest v38 runs. The
> shard driver routes chat through the ordered shortlist
> `deepinfra,relace,wafer` with fallbacks so D127 rotation can engage; the
> first GLM run sets the baseline and verifies the rotation fix against the
> exact DeepInfra 429 `engine_overloaded` failure that dead-lettered 45 R14
> work items. Decision: [D128](../../decisions.md) (decisions log in repo root).
>
> **v38 adjudication rejection-feedback amendment (2026-09-15).** Full-v38 is
> the current protocol. The fact adjudicator retries one deterministic
> translator rejection inside the delivery: on a new-fact target/declare
> mismatch, a supplied-name collision, or incoming support filed as a move,
> drain re-generates once against the same prepared attempt with a structural
> note appended outside the inputs, metered under a distinct
> `:translator-retry1` receipt. A second identical rejection raises exactly as
> before. Relation and observation application generations append
> `:rej-feedback-1`. The base prompt is byte-identical; schema, translator,
> normalizer, and resolver generations stay the same. Adapter identity,
> protocol keys, variants, and fingerprints roll together. Dataset, models,
> budgets, and scoring are unchanged. Stores ingested under v37 are not this
> protocol. Analysis:
> [new-fact references](../analysis/fact_adjudication_new_fact_references_20260915.md).
>
> **v37 adjudication target-discipline amendment (2026-09-15).** Full-v37 added
> two sentences to the fact-adjudication prompt: when target was a supplied
> F-name, `new_facts` had to be empty, and the incoming assertion's placement
> was decided by target/stance alone, never by a `support_moves` entry. That
> addressed the R8 Vertex/Gemma dead-letters (declared-but-unused N-names,
> incoming support filed as a move). Schema and translator stayed the same.
> Relation and observation application generations appended
> `:target-discipline-1`. Normalizer and resolver generations stayed the same.
> The v36 N1/F2 wording and JSON examples were unchanged underneath. Adapter
> identity, protocol keys, variants, and fingerprints rolled together. Dataset,
> models, budgets, and scoring were unchanged. Stores ingested under v36 are
> not that protocol. Analysis:
> [new-fact references](../analysis/fact_adjudication_new_fact_references_20260915.md).
>
> **v36 fact new-fact-reference amendment (2026-09-15).** Full-v36 said to
> choose N1 rather than continue F-numbering, declare that name in `new_facts`,
> and use the same name as `target`. Two complete nine-field JSON examples
> showed structure only: repeating supplied F1 with no other changes, or
> incoming A1 as a different proposition declared as N1. A separate
> Gemma/Vertex control on one captured R7 input had named the new fact F2;
> three diagnostics of this template returned N1 or F1 as required. Invalid F
> references remained rejected. Schema and translator stayed the same.
> Relation and observation application generations appended `:new-fact-refs-1`.
> Normalizer and resolver generations stayed the same. Meaning, temporal,
> schema, retrieval, answer, and judge pins stayed the same. Adapter identity,
> protocol keys, variants, and fingerprints rolled together. Dataset, models,
> budgets, and scoring were unchanged. Stores ingested under v35 are not that
> protocol. Analysis:
> [new-fact references](../analysis/fact_adjudication_new_fact_references_20260915.md).
>
> **v35 nested-normalizer-field amendment (2026-09-15).** Full-v35 named
> existing nested normalizer fields after the root both-array sentence. Each
> observation, relation, and entity reference lists its declared fields;
> empty context uses `[]`; `uses_claim_window` is an explicit boolean;
> `surface=null` when the claim spelling matches the canonical name. That
> completed one captured Gemma/Vertex input that already had the both-array
> sentence; the same input completed in an earlier run, so this is not a
> deterministic failure. Three sequential diagnostics completed that one
> unique input; the third verified raw required fields before Pydantic,
> including three entity references: the subject and two context references.
> Resolver and fact-adjudicator prompt generations stayed the same; the
> normalizer generation appended `:nested-fields-1`. Meaning, temporal,
> schema, retrieval, answer, and judge pins stayed the same. Adapter
> identity, protocol keys, variants, and fingerprints rolled together.
> Dataset, models, budgets, and scoring were unchanged. Stores ingested under
> v34 are not that protocol. Analysis:
> [normalizer_nested_fields_20260915.md](../analysis/normalizer_nested_fields_20260915.md).
> Durable probes:
> [nested-field probes](https://github.com/writeitai/ultimate-memory-cloud/blob/2f64281c/design/analysis/locomo-conv42-gemma-normalizer-nested-probes-20260915.json).
>
> **v34 decision-output-format amendment (2026-09-15).** Full-v34 named
> existing decision-output fields in two processing prompts. The T4 prompt
> requires all four existing fields
> (`candidate_id`, `confidence`, `decision`, `rationale`), including nulls;
> that is a measured Gemma/Vertex completion of one captured input. The fact
> prompt names all nine existing `PromptFactDecision` fields, uses `[]` for
> empty arrays, and says `window=null` when no explicit date replacement is
> intended; that corrects a
> prompt/schema contradiction and is not a reproduced fact-adjudication
> provider failure. Resolver, normalizer, and application generations roll.
> Meaning, temporal, schema, retrieval, answer, and judge pins stay the same.
> Adapter identity, protocol keys, variants, and fingerprints roll together.
> Dataset, models, budgets, and scoring are unchanged. Stores ingested under
> v33 are not that protocol. Analysis:
> [t4_output_format_20260915.md](../analysis/t4_output_format_20260915.md),
> [fact_adjudication_output_format_20260915.md](../analysis/fact_adjudication_output_format_20260915.md).
>
> **v33 normalizer output-format amendment (2026-09-15).** Full-v33 required
> both `observations` and `relations` arrays in one JSON object, using `[]`
> when a kind has no output. Meaning, temporal, schema, retrieval, answer, and
> judge pins stayed the same. Adapter identity, protocol keys, variants, and
> fingerprints rolled together. Dataset, models, budgets, and scoring were
> unchanged. Stores ingested under v32 are not that protocol. Analysis:
> [normalizer_output_format_20260915.md](../analysis/normalizer_output_format_20260915.md).

> **v32 structuring-compatibility amendment (2026-09-15).** Full-v32 changed
> the fallback structuring JSON field to `subsections` while preserving the
> internal `children` tree. Other processing, retrieval, answer, and judge
> pins stayed the same. Adapter identity, protocol keys, variants, and
> fingerprints rolled together. Dataset, models, budgets, and scoring were
> unchanged. Stores ingested under v31 are not that protocol. Analysis:
> [gemma_fallback_subsections_20260915.md](../analysis/gemma_fallback_subsections_20260915.md).

> **Binding D124 retrieval-ablation amendment (2026-09-14).** An additive,
> answer-and-judge-only development runner compares four access profiles over
> one already-processed run: native Codex with local P3, native Codex with P3
> plus the official OSS MCP server, a provider-neutral agent with the OSS MCP
> read surface, and that agent with three P3-like MCP tools. Results and state
> are separate from the canonical run and are not publication scores. The
> Full-v31 protocol is unchanged. Binding contract:
> [`locomo_retrieval_ablation_design.md`](locomo_retrieval_ablation_design.md).

> **D119–D123 processing amendments (2026-09-14).** Full-v29 identifies
> [D119 coherent multi-span extraction](multi_span_claim_extraction_design.md)
> with version reuse. Full-v30 adds [clear processing instructions](processing_prompt_clarity_design.md)
> and [concise adjudication inputs](concise_adjudication_inputs_design.md).
> Full-v31 adds [source-backed document references](document_reference_context_design.md)
> and [contextual fact nomination](contextual_fact_nomination_design.md), with
> separate Selection and Claimify work stages and unchanged two-call extraction.
> The combined extractor, normalizer and adjudicator generations are pinned
> separately from earlier stores. The adapter and protocol fingerprints roll
> together. Dataset, provider defaults, answer/judge behavior and scoring do
> not change. These processing changes do not establish a benchmark-quality
> or billed-cost improvement; the offline byte comparisons are recorded in the
> [implementation evaluation](../implementation_evals/d120_d121_implementation_20260914.md).

> **Binding v28 amendment (2026-09-12).** The protocol pins the D118 fact
> contract ([design](mutable_fact_windows_design.md)): the mutable-window
> normalizer and adjudicator generations, the re-pinned query-space manifest
> hash, and consumption skill 4.0.0. Facts carry one chosen world-time window
> with precision; assured fact results carry `temporal_match`; history mode
> returns completed intervals. Adapter version, protocol identities, and
> fingerprints roll to v28; v27 stores must be re-ingested. Dataset, models,
> budgets, judge rubric, and scoring are unchanged from v27.

> **Binding v27 amendment (2026-09-11).** The protocol pins extractor
> generation `temporal-anchor-4`: E2 writes a resolved relative date into
> claim text as an ISO value and grounds it by the claim's own valid-time
> fields (D41/D32 amendments of 2026-09-11). The answer prompt replaces "use
> timestamps to resolve relative dates" with a plain-word definition of the
> two evidence times — `asserted_at` is when the source made the statement,
> `claim_valid_from` / `claim_valid_until` are when the claim says it happened
> or was true, `claim_valid_kind` names which — and tells the agent that a
> relative phrase still in claim text is unresolved and reads against
> `asserted_at`. Adapter version, protocol identities, and fingerprints roll
> together; stores ingested under v26 must be re-ingested. Dataset, models,
> retrieval behavior, budgets, judge rubric, and scoring are unchanged.

> **Binding D114 / v26 amendment (2026-09-07).** The three assured context
> tools adopt `claims_and_sources_context`, `facts_context`, and
> `combined_context`; the last returns `ContextBundle/v2`. The catalog,
> surface-manifest hash, adapter version, prompt text, protocol identities and
> fingerprints roll together. Dataset, ingestion, models, retrieval behavior,
> budgets, judge rubric and scoring are unchanged.

> **Binding v25 evaluator amendment (2026-09-07).** All default, Gemma/Vertex,
> and Codex-subscription variants reuse one answer-prompt constant. It permits
> general knowledge only as interpretation, forbids seeking benchmark gold
> artifacts, requires content retrieval before every answer, and tells capable
> clients to resolve a material entity in parallel with an independent content
> read before following up by entity id. The existing retrieval default of 50
> is unchanged. The Codex variant pins `gpt-5.6-luna` at reasoning effort
> `high`, supplies no supplemental system instruction, records every returned
> runtime item and each out-of-band action's request metadata in a run-local
> audit, and
> rejects any command, file, MCP, web, or sub-agent action before scoring. It
> remains an experimental, separately fingerprinted protocol and does not
> produce scores comparable to canonical `RS-LoCoMo-Full-v25`. Analysis:
> `plan/analysis/locomo_codex_subscription_bridge.md`.

> **D107 WP-T.4 protocol amendment (2026-09-06).** Full-v24 retains the
> existing query-space contract and rolls extraction to teach all four D41
> kinds and open-ended windows with full source timestamps. Both the default
> and Gemma/Vertex variants use this extraction generation. Protocol identity
> and fingerprints change; earlier protocol scores are directional comparisons.
> Binding extraction semantics: `temporal_clocks_design.md` §6.

> **D118 amendment (2026-09-07; effective when merged).** Future fact-time
> inputs use one chosen window, precision and source timestamps, with explicit
> current/history selection and unknown-date disclosure. Full-v24 and all current
> evaluator variants remain unchanged by this design-only amendment. Roll the
> protocol when replacement semantics ship; see
> [D118](mutable_fact_windows_design.md) and the [delivery plan](../plans/temporal_clocks.md).

> **Historical D107 amendment, WP-T.0b (2026-09-04).** This historical pin was
> `RS-LoCoMo-Full-v23`; the current pin is Full-v24. It retains v22's dataset, rendered documents, models,
> tools, budgets, prompts, scoring, and ingest component versions. The query
> space now publishes `memory_v1.canonical_bounds` and `claims_canonical`, and
> `examples.claims_as_of` overlaps the half-open canonical window and counts
> unknown precision by precision alone (audit 4.21). The surface manifest
> hash, protocol identity, and fingerprint roll; ingestion provenance and the
> library `claims_as_of` result set do not.

> **Historical D107 amendment, WP-T.0a (2026-09-03).** The WP-T.0a protocol was
> `RS-LoCoMo-Full-v22`. It retains v21's dataset, rendered documents, models,
> tools, budgets, prompts, and scoring. Its pinned `adjudicate_observations`
> component version now carries canonical half-open bounds (D107 §5): the
> observation adjudicator compares a day as the whole calendar day and an
> instant as a non-empty point, and `claims_as_of` finds a day-precision
> claim from an intraday window. Ingestion provenance, the library's
> `claims_as_of` result set, protocol identity, and fingerprint roll; the
> benchmark's exposed read-tool set, retry, model-effort, and call-budget
> behavior are unchanged.

> **Historical D106 amendment (2026-09-03; superseded by D107 WP-T.0a).** The
> D106 protocol was `RS-LoCoMo-Full-v21`. It retained v20's dataset, rendered documents, models,
> tools, budgets, answer and judge prompts, counterfactual and complete-answer
> instructions, content-before-`Unknown` harness guard, and no-review scoring
> rule. Its pinned `adjudicate_observations` component version now carries the
> D106 temporal-compatibility rung: dated events with disjoint resolved windows
> never collapse onto or supersede each other (a same-occurrence date dispute
> may contradict), and a dated event is never `evidence` for an undated
> statement. Ingestion provenance, protocol identity, and fingerprint
> roll; no retrieval, retry, model-effort, or call-budget behavior changes.
> V20 and v21 scores are directional because the fact layer differs.

> **Historical D105 amendment (2026-09-01; superseded by D106).** The D105
> protocol was `RS-LoCoMo-Full-v20`. It retained v19's dataset, ingestion, models, tools,
> budgets, counterfactual instruction, content-before-`Unknown` harness guard,
> and no-review scoring rule. Its answer prompt now also requires every
> distinct retrieved value that directly satisfies the question, rather than
> stopping at the first or highest-ranked match. No retrieval, retry,
> model-effort, or call-budget behavior changes.

> **Historical D104 amendment (2026-09-01; superseded by D105).** The D104
> protocol was `RS-LoCoMo-Full-v19`. It retained v18's dataset, ingestion, models, tools,
> budgets, content-before-`Unknown` harness guard, and no-review scoring rule.
> Its answer prompt now explicitly permits bounded counterfactual inference
> from causal or motivational relationships in retrieved evidence and reserves
> `Unknown` for evidence that gives no direction about the dependency. No
> retrieval, retry, model-effort, or call-budget behavior changes.

> **Historical D102 amendment (2026-08-31; superseded by D104).** The D102
> protocol was `RS-LoCoMo-Full-v18`. It retained v17's dataset, models, tools, budgets,
> binary match-biased T4, content-before-`Unknown` guard, and no-review scoring
> rule. Resolver decisions may now use the D102 exact document-local T0 replay
> after a T4 match, changing provider-call counts, resolver/normalizer
> generations, protocol identity, and fingerprint. V17 and v18 scores are
> directional, not a strict protocol A/B.

> **Historical D100 amendment (2026-08-31; superseded by D102).** The D100 protocol was
> `RS-LoCoMo-Full-v17`. It retained v16's dataset, answer/judge models, tools,
> budgets, content-before-`Unknown` guard, and no-review scoring rule while
> rolling the resolver prompt/output schema/generation to one joint binary,
> match-biased T4 call. V16 and v17 scores are directional, not a strict
> protocol A/B.
>
> **Historical D99 amendment (2026-08-28; superseded by D100).** The D99 protocol was
> `RS-LoCoMo-Full-v16`. It retained v15's dataset, models, tools, call budgets,
> and existing two-additional-attempt malformed-reader recovery. The answer
> loop now enforces the already-prompted rule that `resolve_entity` metadata is
> not content evidence: terminal `Unknown` after identity-only reads is rejected
> until one bounded testimony, fact, or combined-context read has been attempted.
> Resolver/convergence generations roll with the release. V15 and v16 scores are
> directional, not a strict protocol A/B.

> **Binding D98 amendment (2026-08-27).** The benchmark no longer builds or
> waits for P2 and does not expose Cypher to the answer agent. Graph questions
> use typed live-graph operations and the bounded PostgreSQL SQL helpers;
> server-owned shallow execution exercises SQL/PGQ. Data-independent readiness
> proves property-graph catalog/function health; the benchmark preflight
> separately proves immediate committed-edge visibility on its isolated sample.
> P3 is
> still explicitly built. D97's changed assured descriptors rolled the
> pre-D99 protocol to `full-v15`; older
> version-transition paragraphs remain historical evidence only.

> **Status:** binding current-system protocol contract. Real provider execution
> remains operator-invoked. Accepting this design does not itself authorize a
> paid run of the current protocol.
