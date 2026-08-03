# Design review — full-scope conventional embedding input architecture

You are an independent **design reviewer** (not the author). Review the proposed
full-scope architecture for RememberStack E1 embedding input. Think at
**high-level product/architecture decisions** as well as concrete mechanisms
(new design section, P1 scalars, work graph, Slack policy, E2 grounding, etc.).

## Working directory

`/Users/jpuc/code/moje/ultimate_memory/ugm_3/ugm`

## Required reading (in order)

1. `plan/analysis/e1_context_prefix_efficiency/FULL_SCOPE_ARCHITECTURE.md` — **primary proposal under review**
2. `plan/analysis/e1_context_prefix_efficiency/PROBLEM.md` — problem frame
3. `plan/analysis/e1_context_prefix_efficiency/SYNTHESIS.md` — earlier multi-agent convergence + owner prefs update
4. `plan/designs/e1_chunks_design.md` — especially §1 three-layer model, §5 embedding granularity / D63 branch, §7 A3 carry-forward
5. Skim: `src/rememberstack/workers/e1.py` (current all-or-nothing prefix+embed), `src/rememberstack/model/chunks.py` (`P1ChunkRow`, `ContextPrefix`)
6. Optional context: D63 in `decisions.md` (embedding port / conventional default)

## Owner constraints (treat as fixed unless you argue they should change)

- **Conventional embedders only** — `texts → vectors`; models must stay **easily interchangeable**
- **Contextual embedding models are a non-goal** for product
- **No hotfixes** — they want proper full-scope contracts and work graph, not “flush every 16 inside the old stage”
- Corpora include **short messages** (Slack, IM) and long chats/papers

## What to opine on (required sections in your report)

Write a single markdown report covering:

### 1. High-level decisions — agree / disagree / amend

For each, state: **Accept**, **Accept with changes**, **Reject**, or **Needs decision**, with rationale.

| ID | Decision proposed |
|---|---|
| H1 | Conventional-only + interchangeable embedders; contextual non-goal |
| H2 | Split “location facts” vs “embedding-input policy” vs “embedding text” (stop overloading `context_prefix`) |
| H3 | Default path: **no LLM** for location; deterministic render only |
| H4 | Location header is **conditional** (body_only vs location_header), not always-on |
| H5 | Short Slack-as-one-doc → body_only + **P1/spine scalars**; long channel export → compact deterministic header |
| H6 | PageIndex **summaries** stay out of default embedding text and out of E2 grounding |
| H7 | Replace document-level all-or-nothing embed with **multi-unit durable graph** (per-chunk resolve/render + batch embed) |
| H8 | Promote this via **new e1 design section** (“Embedding input policy”) + D63/e1 §5 amendment + orchestration update |
| H9 | Prefer long-term: free-form rendered header **out of** E2 grounding union; structured location for E2 instead |

### 2. Mechanism deep-dives (be specific)

For each, give: strengths, risks, alternatives, recommendation.

- **New design section “Embedding input policy”** — scope, knobs (T_short, H_max, α), versioning, re-embed triggers; is this the right home vs retrieval design vs a new standalone design?
- **P1 scalars expansion** (source_kind, channel, user, thread, time, …) — cardinality, index cost, recipe surface, privacy, what belongs in Lance vs Postgres-only vs mounts
- **Storage shape** — keep `context_prefix` vs `embedding_text` + `location_facts` JSON + policy version + text hash
- **Work graph** — separate stages vs one stage with durable sub-stamps; `processing_state` fan-out to chunks vs document + spine stamps
- **Slack / message-atom policy** — is body_only+scalars correct? when does a header still win?
- **Claims vs chunks** — does conditional header interact badly with D58 multi-granularity story?
- **A3 / D56** — carry-forward of embedding_text under policy version; duplicate content at different locations
- **Interchangeability** — does anything in the proposal accidentally re-introduce model-specific behavior?

### 3. What is missing or under-specified

List gaps that would block a binding design (failure modes, migrations, multi-tenant, connector contracts, eval plan, etc.).

### 4. Ranked recommendations to the author

1. Must change before this can become binding design  
2. Should change  
3. Fine as-is / spike later  

### 5. Executive verdict (≤12 lines)

Overall: ship as design direction? major rewrite? fatal flaws?

## Rules

- Be adversarial but constructive. Prefer the simplest design that meets constraints.
- Cite paths/symbols when claiming current code/design behavior.
- Distinguish **analysis opinion** from **already binding design**.
- Do **not** modify production code or binding designs under `plan/designs/`. Analysis/review only.
- Do **not** rubber-stamp: if P1 scalars or a new design section is wrong, say so and propose a better split.

## Output path

Write your complete review ONLY to:

`plan/analysis/e1_context_prefix_efficiency/external_agents/<AGENT>.md`

where `<AGENT>` is `fable` for Claude Fable, or `codex_review` for Codex (this review pass).
