# Independent analysis — E1 context-prefix efficiency

**Author:** Claude Code (independent run)
**Date:** 2026-08-03
**Status:** analysis, non-binding. Nothing here decides D63 or amends
`plan/designs/e1_chunks_design.md`; §7 marks which conclusions *would* need a
design amendment if adopted.
**Companions:** `PROBLEM.md` (problem frame), `internal_analysis.md`
(orchestrator), `SYNTHESIS.md` (convergence, not yet written).

I read the required corpus, then verified every claim about behavior against the
code rather than against the problem frame. Two of my conclusions are *not* in
`PROBLEM.md` or `internal_analysis.md` and change the ranking of the options, so
they are stated up front in §3.6 and §3.7 rather than buried in the option
table.

---

## 1. Problem restatement

**For a reader with no context.** RememberStack turns a document into
`document.md` (one clean Markdown rendering), then into *blocks* (paragraph-grain
deterministic atoms), *sections* (meaning: title, role, summary), and *chunks*
(token-budget runs of whole blocks that never cross a section). Chunks are the
passage-retrieval unit: each one gets an embedding vector, and semantic search
over passages ranks those vectors.

A vector computed from a bare chunk of text knows nothing about where that text
sat. In a 200-turn chat log, "I told her it was fine" is a different fact
depending on which conversation it belongs to, and its vector cannot express
that. The system's answer under the **conventional embedder** branch (the shipped
default, D63) is a **context prefix**: a short LLM-written sentence describing
*where the passage sits* — document title, section, what surrounds it — which is
(a) stored on the chunk row as replayable state and (b) prepended to the chunk
body before the vector is computed:

```
{context_prefix}

{chunk body}
```

The prefix is deliberately **location description only**, never a restatement of
facts (`src/rememberstack/workers/e1.py:345-375` — the prompt says so explicitly,
because the stored prefix later becomes a quotable grounding element in E2 and a
factual prefix would launder second-order content into the evidence chain).

**What broke.** `EmbedChunksHandler.handle`
(`src/rememberstack/workers/e1.py:179-277`) does the whole document in one unit of
work, in this order:

1. load every chunk row for the representation (`:190-195`);
2. **generate every prefix, sequentially, in one generator expression**
   (`:212-221`);
3. embed every fresh chunk in **one** provider call (`:231-247`);
4. only then upsert P1 rows and write the prefixes back (`:251-276`).

Nothing is durable until step 4. On the BEAM 100K smoke ingest the document
packed to **749 chunks**, so step 2 is 749 sequential structured-JSON LLM calls
that must *all* succeed before a single byte lands. One of them returned
non-JSON, the stage failed, the work ledger retried it from the top, and the run
finished in the dead-letter queue with hundreds of billed `prefix:<chunk_id>`
rows in `cost_ledger` and `chunks.context_prefix` still null
(`PROBLEM.md` §2).

**The question.** Keep (or improve) the retrieval quality that context-aware chunk
vectors buy, while making prefix generation fast, cheap, and survivable on first
ingest of documents with hundreds to thousands of chunks — near-term enough to
unblock the BEAM harness this week, and in a shape that is still right at the
millions-of-documents scale the system targets.

---

## 2. Quality goal — what the prefix is *for*, and what "good enough" means

The prefix has **three consumers**, and it is worth separating them because they
want different things and only one of them is the retrieval story:

| # | Consumer | What it uses the prefix for | Code |
|---|---|---|---|
| 1 | **P1 chunk vector** | Prepended to the body; the vector is computed over prefix + body | `e1.py:222-225`, `:231-237` |
| 2 | **P1 chunk text (lexical / agent reading)** | `P1ChunkRow.text` is the same prefix+body string, so the stored passage and any FTS over it include the prefix | `e1.py:251-264` |
| 3 | **E2 grounding union** | The stored prefix is a member of the D32 layer-2 "added context must exist verbatim somewhere source-derived" union, and appears in the extraction bundle | `e2.py:679`, `e2.py:717-718` |

Consumer 3 is the one people forget. It means **changing the prefix changes what
E2 is allowed to say**: shrink the prefix's vocabulary and some previously
accepted `added_context` gets rejected by the grounding gate; widen it and E2 can
quote text that never appeared in the source. Any option below that alters prefix
*content* is therefore not purely a retrieval change.

**What "good enough" means.** Stating this precisely matters, because the current
implementation is being defended on an intuition ("LLM prefixes improve
retrieval") that its own inputs do not support (§3.6):

- **Sufficient:** the embedded string carries enough *deterministic, document-level
  location signal* — document title, human-readable section path, section role,
  position — that two textually similar chunks from different documents or
  different sections separate in vector space, and a query naming the document or
  topic can reach the passage.
- **Necessary:** the stored prefix stays **location description**, contributes no
  facts, and is **stored and carried forward** for unchanged content (A3/D7) so
  later versions never re-pay for it.
- **Not required:** that a language model wrote it. The design says the prefix is
  "a per-chunk LLM call writing 'where this sits'"
  (`e1_chunks_design.md` §5) — an implementation of the goal, not the goal.
  Spike 8 in that same design (`§10.8`) explicitly still owes a measurement of
  "the context prefix's retrieval contribution." **That number does not exist
  yet.** Every quality claim about the current prefix, including mine, is a prior
  awaiting that spike.

**Quality is a property of the embedded string and the stored context, not of the
number of round-trips.** I agree with `internal_analysis.md` §2 on this framing.

---

## 3. Failure modes of the current design

### 3.1 Blast radius is the document, and the unit of durability is the document

`chunks.context_prefix` is only ever written by `ChunkCatalog.record_embeddings`
(`spine/chunk_catalog.py:140-146`), which runs once, at the very end, after every
prefix and every vector exists. `EmbeddingUpdate`
(`model/chunks.py:128-137`) *requires* `embedding_ref` and `embedding_version`
alongside the prefix, so there is currently **no way to persist a prefix without
also having its embedding**. Prefix durability is structurally welded to embedding
completion.

The replay machinery to exploit per-prefix durability **already exists and is
already correct**: `_resolve_prefix` (`e1.py:308-342`) short-circuits on a stored
prefix of the current generation before it will call the model. It is simply never
fed, because nothing writes prefixes mid-flight. This is the single most important
structural fact in the analysis: *the primitive is built, the writer is missing.*

### 3.2 Retry is a full re-run, and it is billed

`max_attempts` defaults to 3
(`spine/migrations/versions/p0_02_0002_infrastructure_registries.py:88`). Each
attempt re-enters `handle` with `chunks.context_prefix` still null, so it
regenerates all 749 prefixes from scratch. Cost attribution is idempotent only
*within* an attempt — `ON CONFLICT (deployment_id, processing_id, attempt,
call_key) DO NOTHING` (`spine/work_ledger.py:808`) — so attempt 2 writes a fresh
`prefix:<chunk_id>` row for every chunk. Three attempts bill three full passes and
land in the DLQ. That is exactly the observed "hundreds of prefix rows, zero
written prefixes."

Note the perverse asymmetry: **the payment is durable and the product is not.**

### 3.3 The arithmetic makes success impossible, not unlikely

`OpenRouterModelProvider._completion_text` makes exactly one provider call per
logical call and does not retry (`adapters/openrouter.py:216-242`, deliberately —
"retrying is the work ledger's job"). So for a per-call failure probability `p`,
the probability the stage completes is `(1-p)^N`:

| per-call failure rate `p` | N=50 | N=200 | N=749 |
|---|---|---|---|
| 0.1 % | 95 % | 82 % | **47 %** |
| 0.5 % | 78 % | 37 % | **2.4 %** |
| 1 % | 61 % | 13 % | **0.05 %** |

A provider that is 99.5 % reliable per call — which is *good* — gives this stage a
1-in-40 chance of ever finishing a 749-chunk document, and three attempts at 1-in-40
is still a dead letter. No amount of provider hardening fixes an architecture whose
success probability decays geometrically in document size. **This is the core
finding: the shape is wrong, independent of how flaky the provider is.**

### 3.4 The specific DLQ error is almost certainly `max_tokens` truncation, not randomness

The observed error is `ContextPrefix: completion content is not JSON (len≈140k)`.
`OpenRouterSettings.max_completion_tokens` defaults to **32 000**
(`adapters/openrouter.py:37`, `:57-65`) and is documented as a *combined
reasoning-and-content* budget. 140 000 characters ÷ 32 000 tokens ≈ 4.4 chars per
token — i.e. the completion is sitting exactly on the cap. `ModelRequest`
(`model/model_provider.py:63-71`) has **no per-request token ceiling**, so a
one-sentence `ContextPrefix` response is issued with the same 32k budget as a
frontier extraction call.

This matters because it changes the failure's *character*: a truncation is
**deterministic for a given prompt at temperature 0**, so retrying the whole stage
re-hits the same chunk and fails identically — which is why three attempts
dead-lettered rather than eventually squeaking through.

**This is a high-confidence hypothesis, not a confirmed fact** — the JSON-decode
error path deliberately reports only a length and digest
(`_content_fingerprint`, `adapters/openrouter.py:375-383`), never `finish_reason`.
It is cheap to confirm, because the failed call's usage *is* already persisted
(`workers/base.py:276-294` meters usage-bearing provider exceptions as
`call_key='provider_failure'`):

```sql
SELECT call_key, tier, tokens_in, tokens_out, model_name
FROM cost_ledger
WHERE processing_id = '<the dead-lettered embed_chunk row>'
ORDER BY occurred_at DESC LIMIT 5;
```

`tokens_out ≈ 32000` confirms truncation. Anything much smaller means a genuine
refusal or prose reply, and §3.3's arithmetic is the whole story instead.

### 3.5 The second scaling wall: one embedding call for the whole document

Even with prefixes fixed, `handle` issues **one** `embed` request containing every
fresh text (`e1.py:231-237`). At 749 chunks of mid-hundreds of tokens that is a
single HTTP POST carrying ~300k tokens against a client with a 120 s timeout
(`adapters/openrouter.py:56`, `:150-154`). Provider batch limits and that timeout
both bite well before 749 inputs. This is the next dead letter after the prefix
one, and it has the same all-or-nothing shape.

There is a trap in the obvious fix: the cost meter uses the fixed
`call_key="embed_chunks"` (`e1.py:238-240`). Splitting into sub-batches while
keeping that key means the `ON CONFLICT … DO NOTHING` at
`spine/work_ledger.py:808` **silently discards every sub-batch's cost after the
first**. Sub-batching must key `embed_chunks:<index>` or ledger discipline
(PROBLEM.md constraint 5) breaks quietly.

### 3.6 The prefix prompt never receives the section title — it receives a numeric path

This is the finding that changes the option ranking, and I did not find it in
either prior document.

`_prefix_prompt` (`e1.py:345-375`) renders `section path {section_path}`.
`section_path` comes from `_SELECT_FOR_EMBEDDING`, which aliases
`s.node_path AS section_path` (`spine/chunk_catalog.py:200-212`). `node_path` is a
**materialized integer path** — `'0.2.1'` — proven by
`_SELECT_SECTIONS`'s own `ORDER BY string_to_array(node_path, '.')::int[]`
(`chunk_catalog.py:171`).

`document_sections.title` exists in the schema
(`p0_02_0003_entities_evaluation_e0_e1.py:490`) and is populated on write
(`spine/document_catalog.py:706-716`). But `_SELECT_SECTIONS` does not select it,
and `SectionSpan` (`model/chunks.py:20-30`) has no `title` field. **The
human-readable section heading is present in the database and dropped on the read
path.**

So the per-chunk call that costs the most and breaks the most is asked "state
where this passage sits" while being handed:

- `title` — the document title (real signal, but constant across all 749 calls);
- `section path 0.2.1` — a number with no semantic content;
- `chunk {ordinal}` — an integer, with no total to compare against;
- section summaries, under an explicit instruction **never to restate anything
  from them**;
- `head` — the **first 400 characters of the chunk body**
  (`e1.py:331`) — which is then concatenated *directly after the prefix* into the
  embedded string (`e1.py:222-225`).

Two consequences follow.

**(a) The marginal information the LLM prefix adds over a deterministic template
is close to zero.** Everything factual it can legitimately say is either constant
per document (title), an integer (path, ordinal), or a paraphrase of text the
vector is about to see verbatim anyway. It is being paid, per chunk, to
re-describe the next 400 characters of its own input.

**(b) The same defect affects E2.** The extraction bundle renders
`SECTION: path {chunk.section_path}, role {chunk.section_role}`
(`e2.py:676`) — also the numeric path. Every claim extracted from this system is
being decontextualized against a section identifier that reads `0.2.1`.

Fixing the read path — select `title`, add it to `SectionSpan`, render an
ancestor **title** path — is a small, independent change that plausibly improves
both prefix quality and extraction quality more than any option in §4, and it must
happen *before* any measurement of "LLM prefix vs template," because right now
that comparison is rigged against the template's real potential and flattering to
nothing.

For the BEAM corpus specifically this is stark: the harness renders each turn as
`## Turn {index}` (`benchmarks/rs_harness_beam/dataset.py:114-118`), so the
section titles are `Turn 1 … Turn N`. With titles wired in, a chat transcript's
prefix would carry turn position — real, useful, deterministic location signal.
Without them it carries `0.17`.

### 3.7 The house already has the right pattern for this exact workload — E1 is the outlier

The D79 section-summary stage (`workers/e0_summary.py`) does *the same class of
work*: many bounded, one-line, orientation-only LLM calls over one document. Its
shape is the opposite of E1's on all four axes:

| | E0 summary (D79) | E1 prefix |
|---|---|---|
| Model seat | flash class — `z-ai/glm-4.7-flash` (`e0_summary.py:159`) | smart reasoning tier — `openai/gpt-5.6-luna` (`e1.py:88`) |
| Concurrency | `ThreadPoolExecutor`, 8 wide (`e0_summary.py:54`, `:303-318`) | strictly sequential generator (`e1.py:212-221`) |
| Per-call failure | **degrades that unit to `None`; the document still succeeds** (`e0_summary.py:591`, `:624` — "summary failure never fails a document") | fails the stage, dead-letters the document, blocks every downstream plane |
| Per-unit reuse | content-keyed cache checked before every call (`e0_summary.py:248`, `:273-285`) | prior-*version* carry-forward only; nothing within the current run |

E2 makes the same point from the other direction. Its handler loops chunk by
chunk making **two** model calls each — 2N calls, more than E1's N — and survives,
because it commits `record_extraction` inside the loop (`e2.py:439-441`) and skips
already-extracted chunks on re-entry (`e2.py:258-262`). E2 is *not* batched in the
implementation despite `e1_chunks_design.md` §6 describing batching; it is simply
**checkpointed**. That is an empirical proof, inside this repo, that **per-unit
commit — not call count — is what makes a many-call stage survivable.**

There is even same-run evidence. In the 401-key smoke recorded in
`design/benchmarks/rs-harness-beam/results/SMOKE_NOTES.md`, `structure` succeeded
while `embed_chunk` dead-lettered, against the *same* dead credential. D79's
degrade-on-failure absorbed the outage; E1's fail-hard did not. (Stated as
inference from the notes plus the code paths above, not from a captured trace.)

### 3.8 A stranded `running` row has no recovery path

Searching the spine, I find no stale-lease reclaim, heartbeat, or requeue for
`processing_state` rows left in `status='running'`; `ops replay` only reopens
`status='dead_letter'` (`spine/operations.py:181-241`,
`spine/work_ledger.py:324-395`). A stage that holds one row `running` for 25–60
minutes (749 sequential calls at 2–5 s each) is a large window in which an OOM
kill, container restart, or routine deploy strands the document with no operator
path back short of manual SQL. Shortening the occupancy is a durability
improvement in its own right, separate from retries.

### 3.9 E1 embed is a serial gate in front of E2 that E2 does not need

`EmbedChunksHandler` chains `extract_claims` (`e1.py:277`, `:445-463`), so
extraction cannot begin until every prefix and every vector exists. Extraction
does not use vectors at all. It reads the prefix, and already tolerates its
absence — `CONTEXT PREFIX: {chunk.context_prefix or '(none)'}` (`e2.py:679`) and
`if chunk.context_prefix:` before adding it to the grounding union (`e2.py:717`).
First-ingest wall-clock is therefore the *sum* of two expensive stages that are
mostly independent, and every prefix flake blocks P1 claims, P2 graph, P3 mounts,
and K1 pages — the entire BEAM surface contract
(`SMOKE_NOTES.md` "Surface contract").

---

## 4. Options

Scored on: **quality** (retrieval + grounding), **cost**, **latency**,
**flakiness**, **D56/A3 fit**, **implementation risk**. "Design amendment?"
answers whether adopting it changes a binding doc (§7 elaborates).

### O1 — Durable per-unit prefix work (checkpointed commit inside the existing stage)

Write each prefix (or each small run of prefixes) to `chunks.context_prefix` as
soon as it is produced, before the next call. On re-entry, `_resolve_prefix`'s
existing stored-prefix branch (`e1.py:323-327`) skips everything already done.

I verified the three things that could have made this unsafe, and none do:

1. **It does not pollute D56 carry-forward.** `_SELECT_CARRY_FORWARD`
   (`chunk_catalog.py:214-230`) requires `embedding_ref IS NOT NULL` *and* a
   matching `embedding_version`. A prefix flushed without an embedding is
   invisible to a later version's carry-forward until the embedding lands. The
   safety property is already in the SQL.
2. **It does not touch identity keys.** `extraction_input_hash` is computed at
   pack time from block hashes and stable header facts (`e1.py:378-421`); no
   prefix participates. PROBLEM.md constraint 4 holds untouched.
3. **It needs no migration.** `chunks.context_prefix` and `prefixer_version`
   already exist; only a `record_prefixes(updates)` method writing those two
   columns is missing from `ChunkCatalog`. `EmbeddingUpdate` cannot express it
   because `embedding_ref` is `_NonEmpty` (`model/chunks.py:128-137`), so a
   sibling value type is needed — a few lines.

| Axis | Assessment |
|---|---|
| Quality | **Identical** — same model, prompt, and stored bytes |
| Cost | Same first pass; retry cost collapses from *N* to *failures only* |
| Latency | Unchanged alone (still sequential); composes with O11 |
| Flakiness | Blast radius per failure: one chunk instead of the document. `(1-p)^N` becomes "resume from where it stopped" |
| D56/A3 | **Fits exactly** — this *is* D7 replay discipline; verified above |
| Risk | **Low.** ~30 lines, no schema change, no new stage, no design amendment. The behavior it enables is already unit-tested for the success path (`src/tests/workers/test_e1_chain.py:313`) |

**Verdict: mandatory hygiene under every other option.** Everything else is a
question of how many calls to make; this is the question of whether making calls
is safe at all.

### O2 — Hierarchical / section-level prefix

One LLM call per section; chunks under it inherit, composed deterministically
(`"{section_prefix} · part {i} of {n}"`).

| Axis | Assessment |
|---|---|
| Quality | Good for documents with meaningful sections; degenerate where sections are one-per-chunk |
| Cost | O(sections). **Zero saving on BEAM**, where `## Turn N` makes sections ≈ chunks |
| Latency | Proportional saving, same caveat |
| Flakiness | Fewer calls, same all-or-nothing shape unless combined with O1 |
| D56/A3 | Workable but adds a *second* carry-forward key (section-level, keyed on a section identity that is explicitly non-deterministic per `e1_chunks_design.md` §1 — sections "never" participate in identity). Non-trivial |
| Risk | Medium; needs a fallback policy when the tree is flat |

**Verdict: not the BEAM answer, and it buys a new carry-forward key for a saving
that is corpus-shaped.** Reconsider after §3.6 is fixed and papers are measured.

### O3 — Deterministic template prefix (no LLM)

`"{title} — {section title path} ({role}), part {i} of {n}"`, built from
`documents.title`, section **titles** (§3.6), `role`, and ordinals.

| Axis | Assessment |
|---|---|
| Quality | **Materially stronger than PROBLEM.md's framing suggests, given §3.6.** Against the *current* LLM prefix it may well be a wash or a win, because the template can carry section titles the LLM prefix never sees. Against a *fixed* LLM prefix, unmeasured. Every input is deterministic and source-derived |
| Cost / latency / flakiness | **Zero LLM, zero latency, cannot fail.** Best on all three |
| D56/A3 | Trivially satisfied — deterministic output, carried forward or recomputed identically |
| Grounding (consumer 3) | **Caveat, and it is a real one.** If the template splices a D79 **section summary** verbatim into the stored prefix, that summary text enters E2's grounding union (`e2.py:717-718`) — which D79 explicitly forbids ("summaries … are never a grounding source", decisions.md D79 clause 5). A titles-and-metadata-only template is safe by construction: heading text comes from `document.md`, so those tokens are already in the source union. **Do not put summaries in a deterministic prefix** unless the prefix is simultaneously removed from `_source_grounding_elements` |
| Risk | Low, given the caveat |

**Verdict: the strongest candidate for the default, and the cheapest thing to
measure. It is currently *under*-rated because nobody noticed the template would
get inputs the LLM does not.**

### O4 — Contextual embedder (D63 alternate branch)

Switch to a voyage-context-class model; delete the prefix stage entirely
(`e1_chunks_design.md` §5 already designs this).

| Axis | Assessment |
|---|---|
| Quality | Potentially best; unvalidated on the D22 golden set |
| Cost | Moves spend to the embedding API |
| Flakiness | Fewest moving parts |
| D56/A3 | Clean — nothing LLM-derived to carry |
| Risk | **No adapter exists.** `src/rememberstack/adapters/` holds `openrouter` and `selfhost` only; the sole `embed` implementation is OpenRouter's OpenAI-compatible route (`adapters/openrouter.py:244-270`). This is a new provider adapter plus a dimension change plus a version-scoped re-embed migration |

**Verdict: correct long-term branch, wrong week.** Not a BEAM unblock. Worth
scheduling as a real evaluation once §5's measurements exist to compare against.

### O5 — Model and protocol hardening

Per-request `max_tokens` for `ContextPrefix`; `reasoning_effort: "none"` for the
prefix seat; flash-class model.

| Axis | Assessment |
|---|---|
| Quality | Unchanged architecture; a flash seat on a one-line location sentence is what D79 already concluded for identical work (§3.7) |
| Cost | Large — a smart-tier reasoning model billed per chunk for one sentence is the wrong seat, and reasoning tokens dominate the bill |
| Latency | Large per-call reduction |
| Flakiness | **Fixes §3.4 specifically** if truncation is confirmed; does nothing for §3.3's geometry |
| D56/A3 | Changes `E1_PREFIXER_VERSION` → a deliberate regeneration boundary, which is correct and intended |
| Risk | Low. `ModelRequest` needs a `max_completion_tokens` field (it has none today — `model/model_provider.py:63-71`); the reasoning-effort map already exists per-model (`adapters/openrouter.py:66-75`) and can be set by env with zero code |

**Verdict: do it, immediately, but never mistake it for the fix.** It raises
`(1-p)^N`'s base; it does not change the exponent.

### O6 — Two-phase embed (embed now, upgrade later)

Land template-prefixed vectors immediately so P1 is live; re-embed with LLM
prefixes when they arrive.

| Axis | Assessment |
|---|---|
| Quality | Temporarily lower, then equal |
| Cost | **Pays for embeddings twice** |
| Latency | Best time-to-first-query |
| D56/A3 | Needs `embedding_version` to distinguish the generations honestly, or recipes silently mix vector generations |
| Risk | Medium — dual-generation vectors in one index is exactly the kind of state that is easy to introduce and hard to reason about later |

**Verdict: only worth it if the LLM prefix is measured to beat the template. If
§5's spike says it does not, O6 is machinery for a benefit that does not exist.**
Do not build it speculatively.

### O7 — Multi-chunk batch prefix (one call returns N prefixes)

| Axis | Assessment |
|---|---|
| Quality | Degrades with batch size (attention dilution — the concern `e1_chunks_design.md` §10.7 already flags for E2 batching) |
| Cost | Fewer round-trips; token count barely moves, since the prompt is already tiny |
| Flakiness | **Worse per unit of progress unless paired with O1** — one malformed element loses the batch. Under strict structured output a partial parse is not available |
| D56/A3 | Neutral |
| Risk | Medium — output/input alignment by index is a classic silent-corruption surface (prefix *k* attached to chunk *j*) |

**Verdict: skip.** It optimizes round-trips, which are not the bottleneck, and it
introduces a misalignment failure mode that is silent rather than loud. If call
count is the problem, O3 takes it to zero.

### O8 — Source-kind policy (chat vs paper)

Different prefix strategy for `source_kind` in {chat, transcript} vs papers.

**Verdict: premature.** It is a branch in the design justified by an
intuition about corpora that no measurement supports yet, and it doubles the
surface that must be evaluated and carried forward. Revisit only if §5's spike
shows the template winning on one corpus and losing on another. Meanwhile the
*correct* corpus-shaped adaptation is already deterministic and free: section
titles (§3.6) make a chat's prefix say "Turn 17" and a paper's say "Results →
Ablations" from the same template.

### O9 — Checkpointed flush every K (same work row)

This is O1 with a batching parameter. `internal_analysis.md` §4 treats it as a
weaker sibling of "true per-unit work"; I disagree with that ordering — see O10.

**Verdict: this *is* the recommendation, at K ≈ 16–32.** Per-flush transaction
cost is amortized while at most K prefixes are ever at risk.

### O10 — Per-chunk `processing_state` rows (true per-unit work)

Fan `embed_chunk` out to one work row per chunk. The schema permits it —
`processing_target` already includes `'chunk'`
(`p0_02_0001_extensions_enums.py:29`) and the unique key is
`(deployment_id, target_kind, target_id, stage, component_version)`, so
document-grain and chunk-grain rows coexist without a migration.

| Axis | Assessment |
|---|---|
| Quality | Unchanged |
| Cost | **Worse.** It destroys the per-document embedding batch (`e1_chunks_design.md` §6's billing-and-lane rule assumes a document-scoped batch), turning one embed call into N, and writes 749 `processing_state` rows plus 749 claim/complete transaction pairs per document |
| Latency | Better *if* multiple workers run; the ledger's claim path is `SKIP LOCKED`, so it parallelizes naturally |
| Flakiness | Best possible blast radius |
| D56/A3 | Neutral |
| Risk | **High.** Orchestration-graph change, chain fan-in problem (who enqueues `extract_claims` when all 749 finish?), and ~10⁸-row `processing_state` growth at the scale this system targets — the same "substrate rows nobody queries individually" objection `e1_chunks_design.md` §2 uses to keep blocks out of Postgres |

**Verdict: do not do this.** It buys a marginal durability improvement over O1/O9
at the price of an orchestration redesign and a row-count regime the design
explicitly avoids elsewhere. This is the one place I diverge from
`internal_analysis.md` §5A, which prefers "true per-unit work" over checkpointing;
at 10⁶ documents × 10² chunks the ledger cost is the deciding term, and O9 gets
~95 % of the benefit for ~5 % of the risk.

### O11 — Bounded in-stage concurrency (new option)

Run the prefix loop through a `ThreadPoolExecutor` of 8, exactly as
`e0_summary.py:303-318` already does for D79 summaries, flushing results per
completed batch (O9).

| Axis | Assessment |
|---|---|
| Quality | Unchanged |
| Cost | Unchanged |
| Latency | **~8× on the dominant term.** 749 sequential calls at 2–5 s is 25–60 min; at 8 wide it is 3–8 min — which also shrinks the §3.8 stranded-`running` window by the same factor |
| Flakiness | Neutral alone; strictly better combined with O1/O9 |
| D56/A3 | Neutral — order-independent, results keyed by chunk id |
| Risk | **Low, and precedented in-repo.** The provider client is `httpx.Client`, which is thread-safe |

**Verdict: take it.** It is the cheapest large latency win available and requires
no new concepts, only the pattern already shipping one stage upstream.

### O12 — Wire section titles into the prefix and bundle read paths (new option)

Select `document_sections.title`, add it to `SectionSpan`, and render an
ancestor-title path instead of `0.2.1` in both `_prefix_prompt` (`e1.py:345-375`)
and E2's bundle (`e2.py:676`).

| Axis | Assessment |
|---|---|
| Quality | **Plausibly the largest single quality gain in this document**, and it improves E2 as well as E1 |
| Cost / latency / flakiness | Zero — one extra column in an existing query |
| D56/A3 | `E1_PREFIXER_VERSION` and `E2_EXTRACTOR_VERSION` both bump (prompt generation changed) → a deliberate regeneration boundary. Correct, and the version strings are built for exactly this (`e1.py:55-72`) |
| Grounding | **Safe** — heading text is source-derived from `document.md`, so adding it to the prefix cannot widen E2's union beyond the source |
| Risk | Low, but it is a real prompt change: bumping `E2_EXTRACTOR_VERSION` re-extracts the corpus. Sequence it deliberately, not accidentally |

**Verdict: do it, and do it *before* measuring anything.** Every comparison in §6
is unsound while the "where this sits" call cannot see where anything sits.

---

## 5. Recommendation

### Primary (ranked, and they compose)

1. **O12 — wire section titles into the prefix and bundle read paths.** The
   cheapest change with the largest plausible quality effect, and a precondition
   for every measurement below. Bump both prefixer and extractor versions
   deliberately.
2. **O5 — right-size the prefix seat.** Per-request `max_tokens` on
   `ContextPrefix` (order 256, not 32 000); `reasoning_effort: "none"` for the
   prefix model; move the seat to the flash class, as D79 already concluded for
   the same class of work. Confirm §3.4 with the `cost_ledger` query first — if
   `tokens_out ≈ 32000`, this alone removes the observed dead letter.
3. **O1 + O9 — checkpointed durable prefixes, flushing every ~16–32.** Add
   `ChunkCatalog.record_prefixes`; flush before continuing. Retry then resumes
   instead of restarting. Verified safe against D56 carry-forward and identity
   keys (§4/O1).
4. **O11 — bounded concurrency of 8** over the prefix loop, same pattern as
   `e0_summary.py`.
5. **Sub-batch the embedding call** (§3.5) at ~64–128 texts with
   `call_key=f"embed_chunks:{i}"`, committing vectors per sub-batch. This is not
   optional dressing; it is the next dead letter.
6. **Adopt D79's degradation policy for the prefix, with a distinct version
   stamp.** After bounded retries, fall back to the O3 deterministic template
   rather than failing the document — the D79 rule ("summary failure never fails a
   document", `e0_summary.py:591`) applied to text of the same kind and stakes.
   Stamp the fallback with a *different* `prefixer_version`
   (e.g. `…:template-fallback`). This is free and self-healing: both
   `_resolve_prefix`'s replay guard (`e1.py:323-327`) and `_SELECT_CARRY_FORWARD`
   (`chunk_catalog.py:214-230`) match on exact `prefixer_version`, so a
   template-stamped row is automatically re-attempted with the LLM on the next
   run and never cemented by carry-forward. Provenance stays honest, degradation
   stays visible, and recovery is automatic.

Items 1–6 are all **pure implementation** (§7) and together take the stage from
"cannot finish 749 chunks" to "finishes in minutes, resumes after failure, and
degrades instead of dead-lettering."

### Secondary (measure, then decide)

7. **O3 — deterministic template as the default**, pending §6's spike. My prior
   after §3.6 is that a titles-based template is at or near parity with a
   *fixed* LLM prefix and clearly ahead of the current one, at zero cost and zero
   flakiness. But `e1_chunks_design.md` §10.8 owes a measurement and I will not
   pre-empt it. If the spike shows parity, O3 becomes the default and the entire
   problem in `PROBLEM.md` disappears rather than being managed.
8. **O4 — contextual embedder** as a scheduled evaluation, not a scramble. It
   needs a new adapter and a re-embed migration; run it against the numbers items
   1–7 produce.

### Explicit "do not do"

- **Do not fan out to per-chunk `processing_state` rows (O10).** Orchestration
  redesign and ~10⁸ ledger rows to marginally beat a 30-line checkpoint.
- **Do not batch N prefixes into one call (O7).** It optimizes the wrong axis and
  adds a silent index-misalignment failure mode.
- **Do not build two-phase embed (O6) before §6's spike.** It pays for embeddings
  twice to preserve a quality delta nobody has measured.
- **Do not splice D79 section summaries verbatim into a deterministic prefix.**
  The stored prefix is in E2's grounding union (`e2.py:717-718`); D79 clause 5
  forbids summaries being a grounding source. Titles and metadata only.
- **Do not delete the prefix stage** for the conventional embedder on efficiency
  grounds alone. PROBLEM.md constraint 6 is right: that is a measured trade, not a
  free one — and O3 gives the efficiency without the deletion.
- **Do not put prefix text into `extraction_input_hash`** in any variant
  (D56/A3, and the ~0 %-reuse hazard `e1_chunks_design.md` §7 names).
- **Do not add "retry the provider call inside the adapter."** The one-call-per-
  logical-call rule (`adapters/openrouter.py:224-228`) keeps billing honest;
  retry belongs to the ledger, and O1 makes the ledger's retry cheap.

---

## 6. Spike plan

### S0 — Confirm the failure mechanism (minutes, no code)

Run the `cost_ledger` query in §3.4 against the dead-lettered row.
**Success:** `tokens_out` is known. If ≈32 000, §3.4 is confirmed and
recommendation 2 is the immediate unblock; if small, §3.3's geometry is the whole
story and recommendation 3 leads. *This is the smallest experiment in the plan and
it retires the largest uncertainty — do it first.*

### S1 — Durability under induced failure (the primary spike)

The smallest experiment that validates recommendation 3, and the one that
converts BEAM from "hope it holds" to "resumes."

- **Setup:** BEAM 100K conversation 1 (the known 749-chunk document). Implement
  O1+O9 (flush every 16). Inject a deterministic fault: fail the prefix provider
  call on chunk 300.
- **Run:** drive `embed_chunk`, let it fail, let the ledger retry.
- **Success criteria**, all four:
  1. After the failed attempt, `SELECT count(*) FROM chunks WHERE
     context_prefix IS NOT NULL AND version_id = …` ≥ 288 (the last flushed
     boundary below 300) — i.e. **progress is durable**.
  2. Attempt 2 issues **≤ N − 288** prefix calls: `SELECT count(*) FROM
     cost_ledger WHERE processing_id = … AND attempt = 2 AND call_key LIKE
     'prefix:%'` — i.e. **completed work is not re-billed**.
  3. The document completes on attempt 2 with all 749 prefixes and 749 P1 rows.
  4. `_SELECT_CARRY_FORWARD` returns zero rows for this lineage while embeddings
     are still absent — i.e. **half-done state never leaks into D56 reuse**
     (this is the safety property §4/O1 claims from the SQL; assert it, don't
     assume it).
- **Then re-run with the worker killed mid-flight** (`docker kill`) rather than a
  clean exception, to expose §3.8: does the row return to a claimable state, or
  strand as `running`? If it strands, that is a separate finding for the
  orchestration owner, and recommendation 4 (concurrency) reduces but does not
  remove the exposure.
- **This spike is the BEAM unblock.** Everything else can follow.

### S2 — Prefix quality: template vs LLM, on fixed inputs

Only meaningful **after O12** lands, which is the point.

- **Arms:** (a) current prefix; (b) title-aware LLM prefix (post-O12);
  (c) deterministic title template, no LLM; (d) no prefix at all (the floor).
- **Corpora:** BEAM 100K subset *and* a paper-shaped document — the corpus
  asymmetry O8 hypothesizes either shows up here or O8 stays unbuilt.
- **Metric:** recall@k on the D22 retrieval golden set, plus the harness's
  `claims_hybrid_rrf` / `claims_verbatim` probes. Report cost and wall-clock per
  arm alongside quality — the decision is a ratio, not a score.
- **Also report:** E2 `grounding_rejected` counts per arm. Changing the prefix
  changes the grounding union (§2, consumer 3), and a template arm that quietly
  triples rejections is not free.
- **This closes `e1_chunks_design.md` §10.8's open measurement** ("the context
  prefix's retrieval contribution"), which is owed regardless of this analysis.

### S3 — Embedding sub-batch sizing

Sweep sub-batch ∈ {32, 64, 128, 256} on the 749-chunk document. Record success
rate, wall-clock, and that `cost_ledger` carries one row per sub-batch (the §3.5
`call_key` trap). Pick the largest size with a clean success rate and margin
against the 120 s client timeout.

---

## 7. Binding design impact

| Change | Classification | Reasoning |
|---|---|---|
| O1/O9 checkpointed prefix commit; `record_prefixes` | **Implementation** | The design says the prefix is "stored and carried forward" (§5, §7/A3). *When within a stage* it is stored is execution detail. No contract, key, or output changes |
| O11 bounded concurrency | **Implementation** | Order-independent; already the house pattern (`e0_summary.py`) |
| Embedding sub-batching + `call_key` fix | **Implementation** | §6's rule is that a batch "never crosses a document"; sub-batching *within* one document preserves it. Document- and lane-level accounting stay exact once call keys are unique |
| O5 model seat / `max_tokens` / effort | **Implementation**, with a version bump | D70 makes per-stage model choice port configuration explicitly. `E1_PREFIXER_VERSION` bumps; that is the designed mechanism, not an amendment |
| O12 section titles in prefix + E2 bundle | **Implementation**, with two version bumps | Fixes a read path against the design's stated intent ("title, section path, orientation"). Bumps `E1_PREFIXER_VERSION` **and** `E2_EXTRACTOR_VERSION` — the latter re-extracts the corpus, so sequence it deliberately |
| Template fallback after bounded retries | **Borderline — flag for the design owner.** | The mechanics are implementation, but it introduces a *second class* of stored prefix (LLM vs deterministic), distinguished by `prefixer_version`. Since D79 already establishes "orientation text degrades, the document survives" as a binding policy, I read this as applying an existing principle rather than creating one. If the owner disagrees, it is a one-paragraph §5 amendment |
| **O3 as the default prefix** | **Design amendment (small)** | `e1_chunks_design.md` §5 and D58 both say the conventional branch means "a per-chunk LLM call". Making the default deterministic changes what the branch *is*. Warranted only on S2 evidence; the amendment would restate §5's branch as "conventional embedder → a stored context prefix (deterministic by default; LLM-generated where measured to pay)" |
| O2 hierarchical prefix | **Design amendment** | Introduces section-level carried-forward LLM state, and sections are the layer the design forbids from carrying identity (§1). Needs its own reuse-key story |
| O10 per-chunk work rows | **Orchestration design change** | New processing-graph shape, fan-in semantics, ledger-scale consequences |
| O4 contextual embedder as default | **Decision (D63 revision)** | Already designed as the alternate branch; *switching the default* is a decision, plus a new adapter and a re-embed migration |
| Removing the prefix from E2's grounding union | **Design amendment (D79-adjacent)** | Only arises if a prefix variant would carry summary text; the titles-only template avoids needing it |

**Nothing in recommendations 1–6 requires a design amendment.** That is the point
of the ranking: the BEAM unblock and the durable long-term shape are the same
work, and it is implementation work.

---

## 8. Open questions

1. **Is §3.4 confirmed?** S0 answers it in minutes and determines whether
   recommendation 2 or 3 is the immediate unblock. Everything downstream is
   cheaper once this is known.
2. **Was dropping section titles from the read path (§3.6) deliberate?** I found
   no comment or decision explaining it, and `document_sections.title` is
   populated. If it was intentional — e.g. a worry about heading text leaking into
   grounding — the reasoning should be written down, because the code reads as an
   oversight and someone will "fix" it.
3. **What does the prefix actually buy?** `e1_chunks_design.md` §10.8 has owed
   this number since D63 resolved the branch. Until S2 runs, the entire
   conventional-plus-prefix economy rests on an untested assumption, and this
   analysis is arguing about how to make an unmeasured thing efficient.
4. **Does E2's grounding union want the prefix at all?** It is the one member that
   is LLM-derived (D79 calls it an "accepted second-order channel"). If S2 shows
   the deterministic prefix at parity, the union becomes fully source-derived —
   a strictly better grounding posture that falls out for free. Worth naming as a
   possible bonus rather than discovering later.
5. **Should `extract_claims` chain from `chunk` instead of `embed_chunk`
   (§3.9)?** It would halve first-ingest wall-clock and remove embedding
   flakiness from E2's path. The blocker is reproducibility, and it is real:
   `extraction_input_hash` deliberately excludes the prefix (D56), so a chunk
   extracted *before* its prefix lands and one extracted *after* share a key while
   having read different bundles. Racing them makes extraction depend on timing.
   Resolvable — either keep the ordering explicit, or drop the prefix from the E2
   bundle entirely (see Q4) — but not by accident.
6. **Is there a stale-`running` reclaim I did not find (§3.8)?** I searched the
   spine and found none, and `ops replay` covers only `dead_letter`. If none
   exists, it is a durability gap independent of E1 and worth its own item.
7. **What is the intended prefix seat?** D70 fixes the *extraction* default at
   `gpt-5.6-luna` and D79 gives summaries a dedicated flash seat, but I find no
   decision covering the E1 prefix seat — `E1Settings.prefix_model` (`e1.py:88`)
   appears to have inherited the extraction default by convenience rather than by
   choice.

---

## Executive recommendation

1. **The shape is the bug, not the provider.** N sequential all-or-nothing calls
   succeed with probability `(1-p)^N`; at N=749 that is arithmetic, not bad luck.
2. **Run the `cost_ledger` query first** — if `tokens_out ≈ 32k`, the dead letter
   is `max_tokens` truncation and a per-request cap unblocks BEAM today.
3. **Ship checkpointed prefixes (~30 lines).** `chunks.context_prefix` and
   `_resolve_prefix`'s replay branch already exist; only a writer is missing, and
   it is provably safe against D56 carry-forward.
4. **Add concurrency-8 and a flash seat** — both are already the house pattern in
   `e0_summary.py` for identical work; E1 is the outlier, not the innovator.
5. **Sub-batch the single 749-input embedding call** — it is the next dead letter,
   and unique `call_key`s are mandatory or the cost ledger silently lies.
6. **Wire section titles into the prefix and E2 bundle.** Today both see
   `path 0.2.1`; `document_sections.title` is populated and dropped on read.
7. **Then measure template vs LLM prefix.** With titles wired, the template may
   simply win — which dissolves this problem instead of managing it.
8. **Nothing in 1–6 needs a design amendment.** The BEAM unblock and the durable
   architecture are the same work.
