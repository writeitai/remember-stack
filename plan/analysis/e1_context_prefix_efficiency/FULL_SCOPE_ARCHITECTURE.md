# Full-scope architecture — conventional embedding input (no hotfixes)

**Status:** non-binding analysis / design-intent draft (2026-08-03)  
**Product constraints (owner):**

1. **Conventional embedders only** — `texts → vectors`; models must stay **easily interchangeable**.  
2. **No contextual-embedding product path** (D63 alternate remains theoretical / non-goal).  
3. **No hotfix mindset** — change the contracts and work graph properly, not “flush every 16 in the same broken stage.”  
4. **Corpora include short messages** (Slack, chat lines, one-liners) as well as long BEAM/chats and papers.

**Supersedes for direction:** the “O1-lite checkpoint the monolith” framing in earlier notes as a *temporary* tactic. That idea becomes a **proper multi-unit pipeline**, not a patch on document-level all-or-nothing.

---

## 1. Problem to solve (properly)

The quality need is real: under a conventional embedder, a bare short string often lacks *coordinates* (which channel, which thread, which section). The **wrong** solution was “always run an LLM to invent a location sentence per chunk, in one document transaction.”

The **right** solution is a **versioned, deterministic embedding-input policy** that:

- builds a **canonical embedding string** from stored structure + body;  
- sometimes **omits** a location header when it would hurt or add nothing;  
- never depends on a special embedder API;  
- makes progress **durable per unit** by architecture.

---

## 2. Core redesign: separate three things that are conflated today

Today `context_prefix` is overloaded: location signal, embed input, P1 text, E2 grounding member, and an LLM product.

**Split them:**

| Concept | What it is | LLM? | Stored where |
|---|---|---|---|
| **Location facts** | Structured coordinates: doc title, source_kind, source_ref, section title path, role, ordinal/N, thread_id, speaker, time range, channel, … | **No** (from E0/E1 structure + connectors) | Spine fields / JSON on chunk or join from sections/docs |
| **Embedding input policy** | Pure function `render_embedding_text(location_facts, body, policy_version) → str` | **No** | Policy is config + version string |
| **Embedding text** | Exact string that was embedded (audit / replay) | No | On chunk (rename from “prefix+body” mental model) |
| **P1 filter scalars** | `section_role`, and extended: source_kind, channel, … | No | Lance columns (already `section_role`) |
| **Section summaries (PageIndex/D79)** | Orientation for humans/agents/nav | Yes (elsewhere) | Section rows — **not** default embed text, **not** E2 grounding |

**Rename in design language (recommended):**

- Stop saying “always generate a context prefix.”  
- Say: **always resolve location facts; conditionally render a location header into embedding text per policy.**

The old `context_prefix` column can remain as **optional rendered header** or be replaced by `embedding_text` + `embedding_input_policy_version` in a binding design. Implementation detail; the contract is the triple (location facts, policy version, embedding text).

---

## 3. When the location header is present vs absent

### 3.1 Principle

The location header is **not a moral requirement**. It is a **collision-reduction device** for conventional vectors.

Include it only when it is expected to **help retrieval more than it dilutes or dominates** the passage.

Needle-finding of atomic facts remains the **claims** channel (D58). Chunks are the **passage** channel. Short Slack lines especially must not have a header longer than the body that swamps the vector.

### 3.2 Policy rules (recommended default product behavior)

Define a single versioned policy, e.g. `embed-input-v1`, applied the same way for every conventional model.

**Step A — Always compute location facts** (deterministic, may be sparse).

**Step B — Decide header mode:**

| Mode | When | Embedding text |
|---|---|---|
| **`body_only`** | Header would add no discriminative signal **or** would dominate a tiny body | `body` |
| **`location_header`** | Multi-context risk is real and we have useful coordinates | `header + "\n\n" + body` |
| **`metadata_only_scalars`** | (Always, in parallel) put role/source_kind/channel/… on **Lance scalars** even when header is omitted | (filter path, not vector text) |

**Concrete predicates for `body_only` (all that match → omit header):**

1. **Single-chunk document** and location facts are empty or only repeat the doc title already equal to body.  
2. **Body token count ≤ T_short** (eval knob; starting hypothesis **32–64 tokens**) **and** the would-be header is ≥ α·body length (α≈1) — header would dominate.  
3. **No useful coordinates:** no title, no section titles, no channel/thread/speaker/time, single synthetic root with no siblings — header would be empty or `"untitled / 0"`.  
4. **Explicit source kinds** that are message-atoms *when ingested as one message = one document*: e.g. `slack_message`, `sms`, `im` **if** channel/user/time are available **as scalars** and the body is already the whole unit — prefer **scalars + body_only** over a prose header, unless the same corpus mixes many channels into one index and measurement shows headers help.

**Concrete predicates for `location_header` (include when true):**

1. **Multi-chunk document** (N_chunks ≥ 2) with a real section title path or real conversation window coordinates.  
2. **Long transcript / BEAM / multi-session chat** packed into one doc — header carries section/window + ordinal.  
3. **Multi-section paper** — header carries title path + role.  
4. Measurement later may add: same body text appears in many places (duplicate blocks) — then position header is mandatory even if short.

**Slack-shaped corpora (explicit behavior):**

| Ingest shape | Behavior |
|---|---|
| **One Slack message = one document, one chunk** | `body_only` for the vector; **always** set scalars: `source_kind=slack`, channel, user, ts, thread_ts. Retrieval uses hybrid: vector on body + filters / BM25. Optional tiny header only if channel+user are *not* filterable and collision is proven. |
| **Channel export = one document, many chunks** | `location_header` with deterministic template: channel, thread, speaker, time range, ordinal — **no LLM**. Short bodies still get a **short** header (fixed field order, hard max header tokens H, e.g. 48). If body &lt; T_short, use **compact header** (channel + speaker + time only), never a multi-line essay. |
| **Thread as document** | Same as channel export at thread grain. |

**Papers / long markdown:** usually `location_header` with title path + role + ordinal.

**Empty body:** do not embed; fail or skip unit with typed reason (no silent empty vectors).

### 3.3 What “no prefix” means operationally

If mode is `body_only`:

- `embedding_text = body` (normalized the same way always).  
- Location is **not lost** — it lives in **spine + P1 scalars** (and P3 path / skill).  
- E2 bundle uses **structured location fields**, not a missing prose prefix.  
- Recipes can filter `source_kind`, `section_role`, channel without polluting short-message vectors.

This is the correct answer to “prefixes should not always be present”: **absence of header ≠ absence of location; location moves to structured metadata when a header would harm short text.**

### 3.4 Hard bounds (policy constants — measure, then freeze)

| Knob | Role | Starting hypothesis |
|---|---|---|
| `T_short` | Below this, prefer body_only or compact header | 48 tokens |
| `H_max` | Max tokens in location header | 48 tokens |
| `α` | Omit header if len(header) ≥ α·len(body) | 1.0 |
| Compact header fields | channel, speaker, time, ordinal | fixed order |

All knobs are part of `embedding_input_policy_version`. Changing them ⇒ re-embed, not silent drift.

---

## 4. Full-scope pipeline (work graph)

Replace one document-level “prefix all then embed all” stage with an explicit graph.

```text
E0 convert + structure (PageIndex snap, D79 summaries)
        │
        ▼
E1 pack chunks (deterministic)
        │
        ▼
┌───────────────────────────────────────┐
│ resolve_location_facts (per chunk)    │  pure join: docs + sections + connector meta
│ durable: write location_facts JSON    │  no LLM
└───────────────────┬───────────────────┘
                    ▼
┌───────────────────────────────────────┐
│ render_embedding_text (per chunk)     │  pure policy function
│ durable: embedding_text + policy_ver  │  mode body_only | location_header
└───────────────────┬───────────────────┘
                    ▼
┌───────────────────────────────────────┐
│ embed_chunk_batch (bounded batches)   │  conventional Embed(texts)
│ durable per batch: P1 + embedding_ref │
└───────────────────┬───────────────────┘
                    ▼
            E2 extract (unchanged grain)
```

### Work-unit rules (architecture, not hotfix)

1. **Unit of durability** = one chunk for location+render; one **batch** for embed (size B, eval knob ~64–128).  
2. Each unit has **ledger state** (either true `processing_state` rows with `target_kind=chunk` / batch id, or an equivalent spine “stage stamp” that is queryable and restart-safe). Document readiness = all chunk units rendered + all embed batches stamped.  
3. **Retry** only unfinished units. Never re-embed a chunk whose `(embedding_input_policy_version, embedding_model, embedding_text_hash)` already matches.  
4. **No LLM** on the default location/render path.  
5. **Failure isolation:** one bad embed batch dead-letters that batch, not the whole doc’s already-written location facts.  
6. **Interchangeable models:** embed stage only sees `list[str]`; swapping model is config + re-embed migration by policy+model version.

This is the proper form of “durable work units.”

---

## 5. PageIndex / summaries — full fit (not optional lore)

| Artifact | Role in this architecture |
|---|---|
| **PageIndex sections** | Supply **title path, role, tree**; packer boundaries; P1 role filter; P3 nav |
| **D79 summaries** | Agent/K orientation, skill “what is this section”; **not** default `embedding_text`; **not** E2 grounding union |
| **Location facts** | May include section title path + role from PageIndex; never require summary text |
| **Short Slack** | PageIndex may be trivial (one section); policy → body_only + scalars; summaries often empty — fine |

Structurer non-determinism stays **out of** D56 identity keys (existing rule).  
`embedding_input_policy_version` **is** part of the embed generation stamp (with model id).

---

## 6. Binding design changes required (when accepted)

These are the **full-scope** updates — not worker band-aids:

### 6.1 `e1_chunks_design.md` §5 (and D63 consequences)

**Replace** “conventional ⇒ per-chunk LLM context prefix stage” with:

- Conventional ⇒ **versioned embedding-input policy** that renders `embedding_text` from **location facts + body**.  
- Default policy is **deterministic**; header **conditional** per §3.  
- Contextual embedder branch: **non-goal / deferred** under product interchangeability requirement (or explicitly “not pursued”).  
- LLM location prose is **not** part of the default path (optional future genre escape hatch only, separate version).

### 6.2 New small design section: “Embedding input policy”

- Modes, predicates, knobs, versioning, re-embed triggers.  
- Slack / message-atom vs transcript-document shapes.  
- Relationship to claims channel (needles) vs chunks (passages).

### 6.3 Orchestration / workers inventory

- Split or redefine stages: `resolve_location` → `render_embedding_text` → `embed_batches`.  
- Document readiness barrier.  
- Cost ledger keys per chunk/batch.

### 6.4 Model / storage contracts

- `embedding_text` + `embedding_text_hash` + `embedding_input_policy_version` (+ model).  
- Location facts schema (typed).  
- P1: vector over `embedding_text`; expand filter scalars for message metadata as needed.  
- E2: consume structured location; **stop depending** on free-form prefix as primary location channel; if a rendered header exists, define whether it stays in grounding union (prefer: **only body + source-derived spans**, header out of grounding — cleaner long-term).

### 6.5 Decision log

- New D-entry or D63 amendment: interchangeability of conventional embedders is a requirement; conditional deterministic location headers; contextual non-goal.  
- A3 still applies to **stored embedding_text** bytes for unchanged content under the same policy version.

---

## 7. What we explicitly do **not** do

| Non-goal | Why |
|---|---|
| Hotfix-only “flush every 16 inside old handle()” as the end state | Doesn’t fix contracts, E2 coupling, or Slack policy |
| Contextual embedders | Breaks easy model interchange |
| Always-on per-chunk LLM location sentences | Cost, flakiness, weak marginal value |
| Always-on long headers on 5-word Slack lines | Header dominates vector; claims channel exists for needles |
| Putting section **summaries** into embedding text by default | Second-order claims in passage index + grounding risk |
| Silent bare embed with no scalars and no policy version | Loses filters and makes A/B impossible |

---

## 8. Implementation program (full scope, ordered)

Still engineering, but **contract-first**:

1. **Spec freeze** — accept this doc’s policy table + stage graph as the intended design amendment draft.  
2. **Schema/contract PR** — location facts + embedding_text + policy version + hashes; migration.  
3. **Policy module** — pure functions + unit tests (Slack one-shot, channel export, paper, BEAM).  
4. **Worker graph PR** — per-chunk resolve/render stamps; batch embed; readiness.  
5. **P1 scalar extension** — channel/source_kind as needed for message corpora.  
6. **E2 bundle cleanup** — structured location; grounding policy clarified.  
7. **Re-embed migration** tool for policy/model bumps.  
8. **Eval** — golden set + short-message corpus + long-chat; compare body_only vs conditional header vs old LLM prefix (control).  
9. **Design promotion** — e1 + decisions.md + worker inventory.  
10. **BEAM / harness** — re-run on proper path (not as the design driver, as the acceptance load).

---

## 9. Behavior cheat-sheet (for implementers)

```text
resolve location_facts(chunk)  # always, durable
header_mode = policy.decide(location_facts, body)

if header_mode == body_only:
    embedding_text = normalize(body)
elif header_mode == location_header:
    header = render_header(location_facts)  # bounded, deterministic
    embedding_text = header + "\n\n" + normalize(body)

store embedding_text, policy_version, text_hash
embed in batches; stamp model+policy+hash
```

**Slack DM one-liner, own document:** body_only + scalars.  
**Slack channel dump, 10k messages as one doc:** location_header compact + ordinal/thread.  
**BEAM multi-batch chat:** location_header with section/window + ordinal.  
**Paper §3.2 paragraph:** location_header with title path + role.

---

## 10. Executive statement

**Proper full scope** is not “faster LLM prefixes.” It is:

1. **Conventional, interchangeable embeddings** over a **canonical embedding string**.  
2. **Deterministic location facts** from structure/connectors (PageIndex titles/roles, Slack metadata).  
3. **Conditional location headers** — present when they disambiguate multi-context passages; **absent** when they would dominate short messages; location still available as **structured metadata**.  
4. **A real multi-unit embed pipeline** with durable per-chunk/batch progress.  
5. **Summaries stay out of default embed text.**  
6. **Binding design updates** to e1/D63/orchestration so this is the system, not a branch of hacks.

That is the behavior set to implement and then promote to binding design.
