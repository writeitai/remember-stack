# E1 — Embedding input policy (Design)

How a packed chunk becomes the **exact string** a **conventional** embedding model
sees, how that string is versioned and reused, and how **location** is carried as
structure (and sometimes as a short header) without special-case embedders or
per-chunk location LLMs.

**Status:** binding design.  
**Decisions:** **D80** (this design); amends consequences of **D63**, **D56/A3**,
**D58**, **D79** consumption; composes **D8**, **D32**, **D37**, **D61**, **D159**.  
**Normative home for embedding-input semantics** (retrieval owns query filters;
E2 owns grounding membership of *typed* location elements).  
**Parent:** `e1_chunks_design.md` §5 (granularity) — this document owns the
**input contract** that §5 previously summarized as “context prefix.”  
**Analysis:** `plan/analysis/e1_context_prefix_efficiency/` (PROBLEM, FULL_SCOPE,
REVIEW_SYNTHESIS, external Fable/Codex reviews).

Numbers (token thresholds, batch sizes) are **starting points to measure**, not
frozen product constants, unless frozen inside a named
`embedding_input_policy_version` artifact.

---

## 1. Product constraints (bound)

1. **Conventional embedders only** as the product path: the embedder port accepts
   ordered `texts → vectors` (plus generation metadata). Embedders must stay
   **interchangeable** under a version-scoped re-embed migration. The
   **embedder generation** pins the model, dimension, distance metric and
   vector-affecting settings, including query/document preprocessing. The
   qualified binding pins request limits (§4.5).
2. **Contextual embedding models** (document-native / late-chunking APIs that make
   a vector depend on undeclared surrounding context) are a **documented product
   non-goal**. Prior design text that described them as a fully designed alternate
   remains historical; new work does not implement or require that branch. See D80.
3. **No per-chunk LLM “where this sits” call** on the default path. Location for
   embedding input is **deterministic**.
4. **Location is always available as structure**; a **location header inside the
   embedding string is conditional** (collision reduction), not mandatory prose.

---

## 2. Three contracts (do not overload one column)

| Contract | Meaning | LLM on default path? |
|---|---|---|
| **Location facts** | Typed coordinates for a chunk occurrence: document title, `source_kind` (connector), **`source_shape`**, section title path, section role, stable position anchors, connector message metadata when present (channel/thread/author refs, timestamps), field **provenance** | No (facts come from spine + connectors; some *upstream* structure may still be model-assisted under D79) |
| **Embedding input policy** | Pure, versioned function: `(location_facts, body) → mode + optional header + embedding_text` | No |
| **Embedding text** | Exact UTF-8 string embedded for the chunk under `(policy_version, body, location_facts)` | No |

**Display / artifact body** (slice of `document.md`) remains distinct from
**embedding text**. `chunk_search` stores **normalized body** for its text/BM25 column (§7);
vectors use embedding text (which may include a header). Lexical search is therefore
not dominated by location headers on short bodies.

**Legacy name:** `context_prefix` in older code/docs means “optional rendered
location header + historical LLM product.” New design language prefers
**location facts** + **embedding text**; migrations may keep a column alias for
the bounded header during transition.

---

## 3. Location facts

### 3.1 Required shape

Each chunk occurrence carries a **versioned location-facts snapshot** (schema
version inside the policy artifact), including at least:

- `doc_id`, `version_id`, `representation_id`, `chunk_id`
- `source_kind` — connector class (`slack`, `google_drive`, `url`, …) as already
  used on documents
- **`source_shape`** — document packaging shape, distinct from connector:
  `document | message_atom | thread | channel_export | transcript | media | other`
- Document title (version-appropriate; see §7 on mutability)
- Section title path (human-readable titles when present; never rely only on
  numeric `node_path` for product behavior)
- Section role
- Stable occurrence anchors preferred over volatile `i of N` (e.g. block-hash
  prefix, message id, time range) — see §6.3
- Optional message metadata when the **connector metadata contract** (§3.3)
  supplies it: stable `channel_ref`, `thread_ref`, `author_ref`, timestamps
- Per-field **provenance**: `source | connector | deterministic_derived | model_derived`

**PageIndex / D79 summaries** may exist on sections for orientation. They are
**not** location-fact fields for embedding input and **not** grounding sources
(D79). They may appear in agent bundles as orientation only.

### 3.2 Connector metadata contract (minimum — implementable)

Message-shaped location is **not** inferred from markdown alone. Connectors that
claim message support must emit the following **minimum** typed payload on the
document version (and, for multi-message docs, on **source-map spans** that cover
blocks/messages). Field catalogs beyond this (Slack-specific extras) are connector
PRs; they must not be required to start D80.

**On every message-capable ingest:**

| Field | Grain | Meaning |
|---|---|---|
| `source_shape` | document/version | `message_atom \| thread \| channel_export \| …` |
| `channel_ref` | document and/or span | stable opaque id in the deployment namespace |
| `thread_ref` | optional | stable opaque id; null if not threaded |
| `author_ref` | span (preferred) or document if single-author atom | stable opaque id |
| `message_ts` / `time_range` | span or document | UTC instant or inclusive range |
| `display_*` | optional | human labels for P3/UI only — **not** P1 filter keys |

**Rules (keep small):**

1. **Stable refs vs display names** — P1 filters use refs; names resolve via PG/P3.
2. **Multi-message chunks** — a chunk covering several messages carries a **set** (or
   ordered list) of span metadata; do **not** collapse to a single author/time. Headers
   and groundable text use a deterministic compact form (e.g. first–last time range,
   author set size, primary channel_ref).  
3. **E0/E1 mapping** — prepare copies available refs into location facts; missing fields
   stay absent (policy must not invent them).  
4. **D159 forget** — purge refs, display maps, and joined authority metadata with the lineage.
5. **Shape choice** (one message = one doc vs export) is **connector + deployment policy**,
   recorded as `source_shape`, not guessed in the embed renderer.

Until a connector implements this, headers/filters use only structure-derived facts
(title, section path, role, `source_kind`).

### 3.3 E2 location elements (wire contract)

E2 always receives a **typed location-element collection**, independent of whether
embedding mode was `body_only` or `location_header`. Free-form embedding headers are
**not** bundle members and **not** in the grounding union.

**Closed element record** (one row per groundable atom):

```text
LocationElement {
  element_id: stable string within the chunk prepare  # e.g. hash(kind|ref|text)
  kind: enum  # document_title | section_title | channel | thread | author | timestamp | source_kind | other_source
  text: non-empty string   # canonical groundable surface form
  provenance: source | connector | deterministic_derived
  locator: optional structured pointer  # source_ref / channel_ref / span id
}
```

**Allowlist for `kind` in v1:** the enum above. No `summary`, no `policy_mode`, no
synthetic ordinals, no free-form “prefix” kind.

**Grounding union membership:**

| In union | Out of union |
|---|---|
| TARGET CHUNK body slice | Free-form `location_header` / legacy `context_prefix` blob |
| Deterministic document header fields that are source-derived | Section **summaries** (orientation only) |
| Same-section neighbour chunk bodies | `model_derived` orientation |
| **LocationElement** rows with allowed provenance | Policy mode labels, pure ordinals |

`added_context[]` tags point at `element_id` / `kind` (advisory attribution may remain);
membership is still **token-in-union** (existing D32 token rule).

---

## 4. Embedding input policy

### 4.1 Artifact

`embedding_input_policy_version` content-addresses:

- location-facts schema version  
- field allowlist and header field order  
- Unicode/whitespace normalization and escaping rules  
- **length counter** identity (policy-owned tokenizer **or** char/byte metric —
  **never** the active embedder’s tokenizer; otherwise mode choice becomes
  model-dependent and breaks interchangeability)  
- mode decision procedure (total function + precedence)  
- constants (`T_short`, `H_max`, α, compact-header field set) when frozen  
- null rendering rules  

Changing any of the above is a **new policy version**.

[D162](split_oversized_chunks_design.md#1-check-the-complete-embedding-input) checks provider
fit (including framing) after D80 chooses the mode and renders the input.

### 4.2 Modes

| Mode | Embedding text |
|---|---|
| `body_only` | `normalize(body)` |
| `location_header` | `normalize(header) + "\n\n" + normalize(body)` with `len(header) ≤ H_max` under the policy counter |

**Always**, independent of mode: location facts exist on the spine. Retrieval
filters join those normalized authority fields; they are not copied into
`chunk_search`.

### 4.3 Decision procedure (total function)

The policy is a **total pure function** of a frozen input snapshot:

```text
(location_facts, body, document_stats, policy_constants)
```

`document_stats` (e.g. `chunk_count`) is snapshotted at prepare time and is part of
the re-render trigger set. The function does **not** read live recipe filter
capability or other undeclared runtime state (that would break purity and
interchangeability).

Precedence is **ordered first match**:

1. If body is empty after normalize → **typed skip** (no embed); readiness records
   the skip code.  
2. If `source_shape = message_atom` and body length ≤ `T_short`:  
   - if compact message coordinates exist (channel/author/time refs) →  
     **`location_header` with compact header** when unfiltered elliptical-message
     eval has not yet passed; after that eval accepts **body_only+scalars**, a
     **new policy version** may select `body_only` here.  
   - else → **`body_only`**.  
   Until the §10 message-atom eval ships, the **bound provisional default** for
   short message atoms **with** coordinates is **compact header** (not body_only),
   so elliptical lines like “yes, ship it” keep location in the vector string.  
3. If no useful coordinates → **`body_only`**.  
4. If multi-chunk document (`chunk_count ≥ 2`) with real section title path or
   transcript/thread/channel_export shape → **`location_header`** (full, or compact
   when body length ≤ `T_short` **or** rendered full header length ≥ α·body length).  
5. If single-chunk long-form `document` with a real title and body length >
   `T_short` → **`location_header`** with title (+ role if present).  
6. Else → **`body_only`**.

**Compact header:** fixed small field set (channel_ref, author_ref, timestamp or
title+role) under `H_max`; never multi-line essays. The α dominance guard is
applied **inside** step 4–5 when choosing full vs compact, not as a separate
unordered rule.

**Starting hypotheses (not frozen until measured and versioned):**
`T_short ≈ 48` policy-counter units, `H_max ≈ 48`, α≈1.0.

### 4.4 Header content rules

- **Include:** title, section title path, role, stable anchors, connector message
  refs when present.  
- **Exclude by default:** D79 summaries, free-form LLM location prose, global
  **`i of N` ordinals** (prepend/insert cascades force document-wide re-embed and
  have weak query value).  
- **Escaping:** source text must not inject fake header fields (delimiter rules
  in the policy artifact).  
- **Mutating titles:** document title used in headers must be pinned to
  **version-scoped** metadata (or a re-render trigger on title change); do not
  silently re-read a live mutable title without a migration event.

### 4.5 Migration triggers and vector reuse

**Three grains — do not collapse them:**

| Name | Grain | Contents |
|---|---|---|
| **embedder_generation** | deployment / model config | model id, dimension, metric, vector-affecting provider parameters |
| **policy_generation** | deployment policy artifact | `embedding_input_policy_version` only |
| **chunk embedding identity** | per chunk | `(chunk_id, policy_generation, embedding_text_hash, embedder_generation)` |

The deployment has one configured `(policy_generation, embedder_generation)`
pair. `chunk_search` is keyed by `(deployment_id, chunk_id)` and stores the pair
and `embedding_text_hash` as attestation, not as permanent key dimensions. A
chunk is semantic-ready only when those values match its prepared authority
stamps and the configured pair.

A prior vector may be reused without a provider call only when its
`embedding_text_hash`, embedder model/dimension, and all policy-relevant
attestation match exactly. The worker may then restamp the same row inside an
explicit unready maintenance operation; it must never claim that a vector was
produced from different text.

| Change | Action |
|---|---|
| Body bytes change | Re-render; new hash ⇒ provider embed under the configured current state |
| Location field that affects header/mode changes | Re-render; new hash ⇒ provider embed |
| Policy version changes | Use the cutover below. Re-render in-scope chunks and reuse exactly attested vectors or re-embed. |
| Filter-only metadata (not in embedding text) | Update authority only; **no** re-embed |
| Embedder generation changes | Use the cutover below to re-embed (§7). |
| Qualified request limits change by themselves | Check passage fit and preserve the query request envelope for online publication. |
| Summary-only regeneration | No render/embed |
| `document_stats` / frozen policy constants change | New policy version (same row as policy change) |

Embedding and extraction carry-forward require both versions to pass the
[D162 reuse gate](split_oversized_chunks_design.md#3-version-wide-reuse-gate)
at commit. Vector carry-forward matches the full render because the location
header can change independently of the body.

Before switching the qualified binding or input policy, durably fence new E1
admission and replay in PostgreSQL for the **maintenance cutover**. Set and
clear the fence in short transactions. Let outgoing work and its handoffs
settle under the outgoing binding.

Serialize D159 unarchive, including source-driven undo, with this fence.
Read the fence under a shared lock held through commit. While fenced, check
lineage state under its row lock before any writes. A live document still
returns a no-op, as does a caller archive when `only_source=True`. An actual
undo returns a retryable maintenance refusal with archive and work intact.
Fence installation waits for earlier unarchive transactions, whose E1 resets
join the outgoing drain. After publication or rollback, unarchive resets and
wakes eligible work under the active binding.

With the fence active, render every persisted chunk needed for rebuild or
eligible replay after release, including completed grids and archived
versions with eligible work. Use the incoming policy and each saved grid's
spans, order and count.
Check each non-skipped input locally against D162's input and singleton-request
limits on every reachable route. If any input fails, report its chunk ID,
refuse the switch and release the fence. Semantic search continues on the
outgoing binding and vectors.
[D162 fit check](split_oversized_chunks_design.md#1-check-the-complete-embedding-input).

Publish a request-limit-only change under the fence with current vector
attestations and semantic search ready if it is local to E1 or preserves the
outgoing query request envelope. For a shared binding, the incoming
per-input and whole-request limits on every reachable query route must still
allow every semantic embedding request the outgoing binding allowed,
including preprocessing and framing. If the envelope would shrink or
compatibility is unknown, refuse the switch and keep the outgoing binding and
vectors. Release the fence after publication or refusal.

Changes that require new attestations, vectors or indexes take the affected
semantic channels unready after fit passes. Rebuild as required, publish the
incoming pair and release the fence after coverage, dimension, policy and hash
checks pass. Discard temporary migration state.

---

## 5. Query filters stay normalized

### 5.1 Principle

Typed filters use existing authority columns and associations in the same
PostgreSQL statement as the ranked search. `chunk_search` does not copy filter
fields merely to avoid joins.

### 5.2 Universal filter dimensions (v1)

- `source_kind` (connector)
- `source_shape`
- `section_role`
- configured policy and embedder readiness

### 5.3 Source-specific filter dimensions (only with recipe support)

Stable deployment-scoped refs such as `channel_ref`, `thread_ref`, `author_ref`,
and time range remain on their typed authority records. Add a conventional
index only when an admitted filter and query plan require it. **Opaque stable
ids** are preferred; mutable display names live in PostgreSQL/P3 reconstruction.

### 5.4 Postgres authority

Full typed location snapshot, provenance, display names, exact timestamps,
id↔name maps. P3/mounts present friendly paths; never sole authority; forget (D159) purges
with lineage.

### 5.5 Claims channel (decision — no open choice)

Claims remain the needle index (D58). Claim retrieval does not inherit or copy
message fields. Recipes that filter by channel/author/time join claim → origin
chunk → typed document/location authority. A later proven query-plan problem may
justify a narrow index; it does not pre-authorize denormalized search columns.

---

## 6. Work graph and durability

### 6.1 Stages (logical)

```text
pack chunks
  → prepare location facts + render embedding text   # pure, durable stamps on chunk
  → embed in bounded batches                         # provider boundary, durable
  → document/representation readiness barrier
  → E2 extract …
```

### 6.2 Durability boundaries

- **Pure prepare** (resolve facts + render): may run inside a document/representation
  transaction writing per-chunk stamps (`location_facts` snapshot ref, mode,
  optional bounded header, `embedding_text_hash`, policy version, location
  elements for E2). Does **not** require one `processing_state` row per pure function.
- **Embed:** durable unit is **chunk embed work** coalesced into **provider batches**
  (size from embedder capabilities; starting hypothesis 64–128 texts). Normative
  batch recovery rules: **orchestration design § embed_chunk (D80)** below —
  single home for call keys and crash recovery.
- **Document readiness:** all in-scope chunks have prepare stamps and successful
  embed matching the configured current state, or an `empty_body` skip. [D162 failures](split_oversized_chunks_design.md#5-terminal-failure).

This replaces document-level “generate all location strings then one giant embed
then write” as the binding execution shape.

### 6.3 No default LLM location path

There is **no** designed default LLM location-prose variant. An experimental
variant would need its own policy version, durability, and eval — until then it
is a **non-goal**, not a “later” hedge in binding text.

---

## 7. Storage (D37 discipline)

Chunk authority rows hold **keys and stamps**, not a second full body:

- location-facts snapshot + **LocationElement[]** (or equivalent) for E2  
- optional **bounded header** string (`location_header`; not body)  
- `embedding_text_hash`, `embedding_input_policy_version` / policy_generation  
- current embedder/model attestation
- offsets into `document.md` for the body (existing)

**`chunk_search.search_text` (bound decision — no implementer choice):** store the
**normalized body only** (same bytes as `document.md` slice after normalize).  
Do **not** put `location_header` into the P1 text/BM25 column. Vectors are still
computed over **embedding text** (header+body when mode says so); the header is
retained on the PG stamp and returned **separately** on hydration when present.
That keeps short-message BM25 from being dominated by headers and matches
“header returned separately from source body” in retrieval design.

One active row is keyed by `(deployment_id, chunk_id)`. The
[maintenance cutover](#45-migration-triggers-and-vector-reuse) governs semantic
readiness and publication.

---

## 8. Interchangeability checklist

A deployment may swap conventional embedders when:

1. Policy is model-independent (counter, normalization).  
2. Embedder generation records model identity, dimension, metric, and relevant
   params.  
3. Re-embed maintenance + unready/rebuild/verify/publish behavior exists and is drilled.
4. No code path branches embedding **text** on model id.

---

## 9. Decision interactions

| Decision / design | Effect |
|---|---|
| D63 | Default remains conventional hosted/self-host embedders; **product path drops contextual alternate**; input policy replaces “per-chunk LLM prefix stage” |
| D56 / A3, D162 | Extraction keys exclude LLM output. Both versions must pass the [D162 gate](split_oversized_chunks_design.md#3-version-wide-reuse-gate) at commit. Vector reuse also requires `embedding_text_hash`, policy and embedder generation. |
| D58 | Claims = needles; chunks = passages; normalized joins apply role filters |
| D79 | Summaries orientation-only; **not** default embedding text; **not** grounding |
| D32 / E2 | Typed groundable location elements; free-form header out of union |
| D37 / D94 | Exact source bodies stay in artifacts; one normalized current chunk body lives in `chunk_search` |
| D61 | Connector metadata port for message facts |
| D159 | Forget purges location metadata + `chunk_search` with lineage |
| D8 / retrieval | Scalar filter operators declared per recipe |
| Orchestration | Prepare stamps + batch embed + readiness barrier |

---

## 10. Spikes / acceptance (measure before freezing knobs)

1. **Policy counter pin** — char/byte vs policy tokenizer; freeze in version.  
2. **Header contribution** — body_only vs compact header vs full header on long
   chat + paper golden sets (closes former “prefix quality” spike under D63).  
3. **Message atoms** — filtered vs unfiltered retrieval for elliptical short
   messages; gate on whether body_only+scalars is non-inferior.  
4. **Fault injection** — kill mid-embed batch; no re-embed of completed chunks;
   cost keys unique.  
5. **Reuse locality** — edit that moves a chunk’s section/channel changes
   embedding_text_hash; content-only body edit without location change reuses
   vector when hash matches.  
6. **Binding cutover** — exercise
   [fit refusal, online limit changes and successful rebuild](split_oversized_chunks_design.md#6-acceptance).
7. **D159** — forget purges derived search state and location facts.

---

## 11. Implementation non-goals

- Hotfix-only “flush every N inside the old monolithic handler” as the end state  
- Contextual embedder product support  
- Default per-chunk LLM location sentences  
- Ungoverned connector JSON in P1
- Global `i of N` in default headers  
- Storing full embedding text in PG for every chunk  
- Claiming scalars fix retrieval without recipe/filter support  

---

## References

- `e1_chunks_design.md` §5–§8  
- `e2_e3_claims_relations_design.md` (grounding union)  
- `retrieval_design.md` (channels, filters)  
- `orchestration_design.md` (stages, readiness)  
- `postgres_schema_design.md` (D37 keys vs bodies)  
- `plan/analysis/e1_context_prefix_efficiency/`  
- Decisions: **D80**, D56, D58, D63 (amended), D79, D8, D32, D37, D61, D159  
