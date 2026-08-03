# E1 — Embedding input policy (Design)

How a packed chunk becomes the **exact string** a **conventional** embedding model
sees, how that string is versioned and reused, and how **location** is carried as
structure (and sometimes as a short header) without special-case embedders or
per-chunk location LLMs.

**Status:** binding design.  
**Decisions:** **D80** (this design); amends consequences of **D63**, **D56/A3**,
**D58**, **D79** consumption; composes **D8**, **D32**, **D37**, **D61**, **D74**.  
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
   **interchangeable** under a version-scoped re-embed migration (model, dimension,
   distance metric, provider caps, and any query/document preprocessing are part of
   the **embedder generation**, not silent globals).
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
**embedding text**. P1 passage search may store embedding text for the vector
channel; lexical/FTS and agent reading should not silently require location
headers to dominate short bodies (policy decides).

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

### 3.2 Connector metadata contract (prerequisite for message corpora)

Message-shaped location (Slack channel, user, thread, message time) is **not**
inferred from markdown alone. It requires a typed extension of the watched-source
/ ingest contract (D61 family):

- Connectors emit **structured metadata** alongside `SourceItem` / document
  lineage (stable refs, not only display names).
- E0/E1 map that metadata into location facts on the chunk occurrence.
- Hard-forget (D74) purges metadata with the lineage.

Until a connector implements the contract, policy **must not pretend** those
fields exist: `source_shape` may still be set (e.g. `transcript` for a pasted
export), and headers use only available facts.

**Who chooses shape** (one Slack message = one document vs channel export):
**connector + deployment ingest policy**, recorded as `source_shape` on the
document/version — not guessed inside the embed renderer.

### 3.3 Provenance and grounding (E2)

E2 decontextualization needs **source-derived** location tokens in the
**grounding union** when claims assert location (“Alice said X in #eng”).

**Bound rule (amends naive “drop prefix from grounding”):**

| Element | In E2 grounding union? |
|---|---|
| Free-form rendered location **header** string | **No** (not as a blob) |
| Typed allowlisted location elements with `provenance ∈ {source, connector, deterministic_derived}` and stable text (title, channel name/ref display form defined by contract, author, timestamps, section titles from source headings) | **Yes** |
| `model_derived` orientation, section **summaries**, synthetic policy mode labels, pure ordinals | **No** |

Removing free-form `context_prefix` from the union without this typed replacement
is **incorrect**. Implementation: structured elements in the union (parallel to
`document_header`), not re-admission of LLM prose.

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

### 4.2 Modes

| Mode | Embedding text |
|---|---|
| `body_only` | `normalize(body)` |
| `location_header` | `normalize(header) + "\n\n" + normalize(body)` with `len(header) ≤ H_max` under the policy counter |

**Always**, independent of mode: location facts exist on the spine; **P1 filter
scalars** (§5) may be projected for query-time filters.

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

### 4.5 Migration triggers and vector reuse (single rule)

**Passage representation generation** is the composite identity:

```text
passage_generation = (
  embedding_input_policy_version,
  embedding_text_hash,
  embedder_generation
)
```

A prior vector may be **attested into** a new passage generation **without a
provider call** only when `embedding_text_hash` and `embedder_generation` both
match and the implementation records the new policy version on a **new**
generation record (zero-call copy with provenance). It must **not** overwrite a
single row so that policy version and vector identity disagree.

| Change | Action |
|---|---|
| Body bytes change | Re-render; new hash ⇒ provider embed under active generations |
| Location field that affects header/mode changes | Re-render; new hash ⇒ provider embed |
| Policy version changes | Re-render all in-scope chunks; if hash unchanged and embedder generation unchanged, **zero-call vector attestation** into the new `passage_generation`; else provider embed |
| Scalar-only metadata (not in embedding text) | Update P1 scalar projection / PG; **no** re-embed |
| Embedder generation changes | Provider re-embed + generation-safe P1 cutover (§7) |
| Summary-only regeneration | No render/embed |
| `document_stats` / frozen policy constants change | New policy version (same row as policy change) |

Content-hash-only vector carry-forward is **insufficient** when embedding text
includes location. Block-level **extraction** reuse (D56 A1–A3) remains
content-addressed without LLM output in identity keys.

**Dual-generation cutover:** during re-embed, PG and P1 hold **versioned per-chunk
embedding records** (or an equivalent generation manifest). Query targets an
**active passage_generation pointer** at deployment/query scope. Cutover flips
the pointer only when required records exist; old generation remains until
retirement. Do not use sole in-place upsert-by-`chunk_id` as the migration story.

---

## 5. P1 scalars (filters), not a metadata landfill

### 5.1 Principle

Scalars enable **typed prefilters** on the passage channel. A scalar that no
recipe can filter on does **not** satisfy the retrieval contract.

### 5.2 Universal Lance dimensions (v1)

- `source_kind` (connector)  
- `source_shape`  
- `section_role` (existing D58 pattern)  
- active **embedder generation** id (for generation-safe search)

### 5.3 Source-specific Lance dimensions (only with recipe support)

Stable deployment-scoped refs: `channel_ref`, `thread_ref`, `author_ref`, and/or
time range — bitmap/range indexes by measurement. **Opaque stable ids** preferred;
mutable display names live in Postgres/P3 reconstruction.

### 5.4 Postgres authority

Full typed location snapshot, provenance, display names, exact timestamps,
id↔name maps. P3/mounts present friendly paths; never sole authority; D74 purges
with lineage.

### 5.5 Claims channel

Claims remain the needle index (D58). Whether claim rows inherit a subset of
message scalars is a retrieval join choice: document either **inherit on claim
rows** for hot filters or **join chunk→doc** in recipes — pick one in retrieval
design when implementing; do not leave it implicit.

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
  optional bounded header, `embedding_text_hash`, policy version). Does **not**
  require one `processing_state` row per pure function.
- **Embed:** durable unit is **chunk embed work** coalesced into **provider batches**
  (size from embedder capabilities; starting hypothesis 64–128 texts). Each batch
  has unique cost-ledger `call_key`s; poison isolation splits bad batches.
- **Cross-store order:** upsert P1 for the batch, then stamp PG embedding refs /
  generation; retries are idempotent; readiness rejects mixed generations for a
  query generation.
- **Document readiness:** all in-scope chunks have prepare stamps and successful
  embed under the active generation (or typed skips).

This replaces document-level “generate all location strings then one giant embed
then write” as the binding execution shape.

### 6.3 No default LLM location path

There is **no** designed default LLM location-prose variant. An experimental
variant would need its own policy version, durability, and eval — until then it
is a **non-goal**, not a “later” hedge in binding text.

---

## 7. Storage (D37 discipline)

Postgres holds **keys and stamps**, not a second full body:

- location-facts snapshot (typed; may be TOAST-bounded JSON with schema version)  
- optional **bounded header** string (not full body duplicate)  
- `embedding_text_hash`, `embedding_input_policy_version`  
- embedder generation / `embedding_ref` pointers  
- offsets into `document.md` for the body (existing)

**Full embedding text** for serving lives with the vector estate (P1) as today
for passage text+vector; do not require PG to store `header+body` for every chunk
at corpus scale.

**Generation-safe migration:** re-embed builds a new P1 generation; cutover is
atomic at query generation, not silent in-place overwrite of the only row without
a generation key (amends naive upsert-by-chunk_id as the sole migration story).

---

## 8. Interchangeability checklist

A deployment may swap conventional embedders when:

1. Policy is model-independent (counter, normalization).  
2. Embedder generation records model identity, dimension, metric, and relevant
   params.  
3. Re-embed migration + P1 generation cutover exist and are drilled.  
4. No code path branches embedding **text** on model id.

---

## 9. Decision interactions

| Decision / design | Effect |
|---|---|
| D63 | Default remains conventional hosted/self-host embedders; **product path drops contextual alternate**; input policy replaces “per-chunk LLM prefix stage” |
| D56 / A3 | Extraction reuse unchanged (no LLM in identity keys). **Vector** reuse requires embedding_text_hash + policy + embedder generation |
| D58 | Claims = needles; chunks = passages; scalars extend role-filter pattern |
| D79 | Summaries orientation-only; **not** default embedding text; **not** grounding |
| D32 / E2 | Typed groundable location elements; free-form header out of union |
| D37 | No full embedding body in PG |
| D61 | Connector metadata port for message facts |
| D74 | Purge location metadata + P1 scalars with lineage |
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
6. **Two conventional embedders** — swap generation; query generation cutover.  
7. **D74** — forget purges scalars and facts.

---

## 11. Implementation non-goals

- Hotfix-only “flush every N inside the old monolithic handler” as the end state  
- Contextual embedder product support  
- Default per-chunk LLM location sentences  
- Ungoverned connector JSON in Lance  
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
- Decisions: **D80**, D56, D58, D63 (amended), D79, D8, D32, D37, D61, D74  
