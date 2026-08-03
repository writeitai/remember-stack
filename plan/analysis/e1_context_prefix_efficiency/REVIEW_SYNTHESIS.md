# Review synthesis — Fable + Codex on FULL_SCOPE_ARCHITECTURE

**Date:** 2026-08-03  
**Primary proposal:** `FULL_SCOPE_ARCHITECTURE.md`  
**Reviews:** `external_agents/fable.md`, `external_agents/codex_review.md`  
**Status:** non-binding

## Overall verdict (both)

**Ship as design direction with amendments — not a rewrite, not a rubber stamp.**

Neither reviewer rejects the core: split location facts / policy / embedding text;
deterministic default path; conditional headers; conventional interchangeable embedders;
replace document-level all-or-nothing embed durability.

## High-level decisions at a glance

| ID | Topic | Fable | Codex |
|---|---|---|---|
| H1 | Conventional-only; contextual non-goal | Accept w/ changes (demote, don’t erase D63 alternate text) | Accept (amend D63 explicitly; define interchangeability fully) |
| H2 | Split facts / policy / embedding text | **Accept** | Accept w/ changes (also split display body vs embed text; typed groundable location) |
| H3 | No LLM on location path | Accept w/ changes (freeze machinery; measure default content; explicit LLM escape hatch or non-goal) | Accept w/ changes (field provenance; upstream structure may still be LLM) |
| H4 | Conditional header | Accept w/ changes | Accept w/ changes |
| H5 | Slack body_only + scalars | Accept w/ changes; **metadata contract missing** | **Needs decision** (eval first; scalars ≠ unfiltered search) |
| H6 | Summaries out of embed + grounding | **Accept** | **Accept** |
| H7 | Multi-unit durable graph | Accept w/ changes (durable at failure boundaries, not every pure fn) | Accept w/ changes (hybrid: prepare in doc txn; durable embed units) |
| H8 | New e1 design section + amendments | Accept w/ changes (wider amendment surface) | Accept w/ changes (home in e1 §5; not a second standalone design) |
| H9 | Header out of E2 grounding | **Accept w/ load-bearing fix** | **Accept w/ changes** (typed groundable set, not bare removal) |

## Must fix before binding (intersection)

1. **H9 — E2 grounding**  
   Do **not** remove location from grounding without a replacement.  
   Free-form rendered headers out; **typed, source-derived location elements in** (channel, author, time, title when provenance is source/connector).  
   Why: decontextualized claims need location tokens; `body_only` makes this *more* important.

2. **Connector / message metadata contract**  
   Proposal assumes channel/user/thread/time exist. Current `SourceItem` does not carry them.  
   Full scope includes a **D61-class connector metadata** design + storage + threading into location facts — not “P1 scalars” alone.

3. **Policy must be model-independent**  
   `T_short` / `H_max` / α must use a **policy-pinned counter** (or char/byte bounds), never the active embedder’s tokenizer — or interchangeability breaks.

4. **D56 / location-sensitive reuse**  
   Content-hash-only carry-forward can serve a **stale location** if the same body sits in a new place, or can ignore location.  
   Reuse/re-embed must account for **location-affecting fields + policy + text hash + embedder generation** (details differ slightly between reviewers; both reject naive content-only reuse for location-aware vectors).

5. **Executable policy + migration table**  
   Total function (precedence of rules), source_shape vs source_kind, re-render vs re-embed vs scalar-only update, generation-safe P1 cutover (Codex: don’t only upsert-by-chunk_id in place for migrations).

## Should change (strong agreement)

| Topic | Agreement |
|---|---|
| **Design home** | Normative text in **e1 §5 / embedding input policy subsection**; not a free-floating second design; cross-link retrieval, E2, orchestration, schema |
| **P1 scalars** | Yes, but **small universal set** + measured source-specific refs; Postgres owns full typed snapshot; Lance gets filter keys recipes actually use; stable IDs not mutable display names; hard-forget paths |
| **Work graph** | Durable units at **provider/failure boundaries** (esp. embed batches / per-chunk embed identity); pure resolve/render can be in-document prepare without three ledger stages |
| **Storage** | Avoid storing full `embedding_text` body-duplication in PG at 10⁸ scale (D37); prefer facts + optional bounded header + hash; vectors/text authority in P1/artifacts as schema already intends |
| **Ordinal i/N in header** | Re-embed cascade risk on prepends; little query value — prefer stable coordinates |
| **H5 Slack default** | Keep shape-aware idea; **don’t freeze body_only for atoms** without filtered vs unfiltered eval (Codex harder than Fable) |
| **LLM escape hatch** | Either design it fully or document non-goal — no “later” hedge in binding text |

## Fine as-is

- Core triple split (facts / policy / embedding text)  
- Summaries out of default embed text and grounding (H6)  
- Conditional header principle  
- Conventional interchangeability as product constraint  
- No hotfixes as program framing  

## Implications for “new design section” and “P1 scalars”

**New design section:** Both say **yes**, housed under **E1**, as the pure policy + modes + versioning + migration triggers. Fable/Codex both warn the *amendment blast radius* is wider than one section (D56, E2, retrieval recipes, schema, orchestration).

**P1 scalars:** Both say **yes with discipline** — not “dump all connector JSON into Lance.” Scalars are inert without recipe/filter contracts; universal low-cardinality dims first; message refs only when search will use them; privacy/forget included.

## Next author actions (after this review)

1. Revise `FULL_SCOPE_ARCHITECTURE.md` (or draft e1 amendment) incorporating H9 structured grounding, connector metadata port, policy counter, D56 reuse, storage discipline.  
2. Decide H5 after a written eval plan (or provisional “needs measurement” in binding text).  
3. Then promote to binding e1 §5 + decisions — not before.
