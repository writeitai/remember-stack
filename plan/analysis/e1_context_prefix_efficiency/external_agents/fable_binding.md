# Fable — binding review of D80 / e1 embedding-input policy (PR pass)

**Reviewer:** Fable (independent design reviewer)
**Date:** 2026-08-03
**Status:** analysis, non-binding. Review of the *promoted binding* docs, per
`PROMPT_REVIEW_BINDING.md`. No designs or code modified.
**Corpus reviewed (binding):** `plan/designs/e1_embedding_input_policy.md`;
`decisions.md` D80 + D63 amendment + D79 supersession note;
`plan/designs/e1_chunks_design.md` §5/§7/§8; `plan/designs/e2_e3_claims_relations_design.md`
(D80 amendment); `plan/designs/retrieval_design.md` (D80 amendment);
`plan/designs/orchestration_design.md`. Cross-checked against
`plan/designs/postgres_schema_design.md` and `plan/designs/e0_files_design.md` (binding,
not on the required list — this is where the problems are). **Analysis (non-binding)
consulted:** `REVIEW_SYNTHESIS.md`, `PROBLEM.md`, prior external reviews, `plan/analysis/workers.md`.

---

## 1. Verdict

**Accept as binding with required amendments.**

The new design (`e1_embedding_input_policy.md`) is the strongest document in this program:
the three-contract split, the typed grounding replacement, the total-function policy, the
durable work graph, and the interchangeability checklist are all correctly bound, and every
intersection must-fix from the first review landed *in that document*. What has **not**
happened is the amendment sweep both prior reviewers explicitly warned about ("the amendment
blast radius is wider than one section" — `REVIEW_SYNTHESIS.md` §Implications). Two binding
designs outside the required reading list still bind the superseded architecture verbatim —
`postgres_schema_design.md` ships DDL for the LLM `context_prefix`, and `e0_files_design.md`
still binds the summary→prefix channel D80 supersedes — so the corpus currently contains
binding-vs-binding contradictions. Plus one genuine internal inconsistency in the vector-reuse
rule. All required amendments are text-sized; none threatens the architecture.

---

## 2. Prior must-fix checklist (first review → binding text)

| # | Item | Status | Citation |
|---|---|---|---|
| 1 | **H9 typed groundable location** (not bare header removal) | **Landed** | `e1_embedding_input_policy.md` §3.3 (provenance-allowlisted table; "Removing free-form `context_prefix` from the union without this typed replacement is **incorrect**"); `e2_e3_claims_relations_design.md` §3.3 D80 amendment; D80 clause 5 |
| 2 | **Connector metadata contract** called out | **Landed** | policy §3.2 (typed D61-family extension; stable refs not display names; `source_shape` ownership = connector + ingest policy; "policy must not pretend those fields exist"; D74 purge); D80 clause 8 |
| 3 | **Model-independent policy length counter** | **Landed** | policy §4.1 ("policy-owned tokenizer **or** char/byte metric — **never** the active embedder's tokenizer"); §8 checklist item 1; §10 spike 1 |
| 4 | **D56 vector reuse** = text hash + policy + embedder generation | **Landed, with an internal inconsistency** (finding M3 below) | policy §4.5; `e1_chunks_design.md` §7 "Passage embedding reuse (D80 refinement)"; D80 clause 6 |
| 5 | **Work graph at failure boundaries** (not pure-fn ledger spam) | **Landed** (logical shape); residual: ledger mapping unbound (finding S3) | policy §6.1–§6.2 (pure prepare in doc/representation txn, no per-pure-fn rows; durable capability-bounded batches; unique `call_key`s; poison isolation; readiness barrier); `orchestration_design.md` cold-read note + preamble |
| 6 | **Storage D37** (no full body in PG) | **Landed in policy; contradicted by unamended schema design** (finding M1) | policy §7 (keys and stamps, bounded header only, full text with the vector estate); `e1_chunks_design.md` §8; *contradicted by* `postgres_schema_design.md` §7 chunks DDL |
| 7 | **Slack body_only provisional + eval gate** | **Landed** | policy §4.3 rule 2 ("**provisional default**; acceptance requires eval on filtered *and* unfiltered retrieval for elliptical messages"; failure fallback = compact header); §10 spike 3 |
| 8 | **No global i/N in default headers** | **Landed** | policy §4.4 (excluded, with the prepend-cascade rationale stated); §11 non-goals; stable anchors §3.1 (nit: §3.1's "see §6.3" cross-reference points at the no-LLM section, not the anchor/header rules — broken ref) |
| 9 | **Design home under e1 / D80** | **Partial** | Home landed: standalone doc but properly anchored (`e1_embedding_input_policy.md` header declares "Parent: `e1_chunks_design.md` §5"; e1 §5 defers to it as normative; D80 records it). Codex's literal ask ("not a second standalone design") was not followed, which I consider acceptable — the intent (one normative home, cross-linked) is met. **What is Partial is the blast-radius sweep the home question was really about**: E2, retrieval, orchestration, e1 were amended; `postgres_schema_design.md` and `e0_files_design.md` were not (findings M1, M2) |

---

## 3. High-level decisions as now written (residual verdicts)

- **H1 — conventional only; contextual non-goal: Accept.** Bound in policy §1, D80, D63
  amendment, e1 §5. My prior "demote, don't erase" landed exactly right: D63's original text
  stands with a dated amendment note; e1 §5 marks the historical alternate "non-operative for
  product work," not deleted. Interchangeability is defined operationally (§8 checklist),
  which is what makes the non-goal enforceable rather than aspirational.
- **H2 — facts / policy / embedding-text split: Accept.** Policy §2, including the
  display-body vs embedding-text distinction and the legacy-name note.
- **H3 — no LLM on the location path: Accept.** §1.3 + §6.3 states it as a non-goal with the
  experimental-variant conditions spelled out — no "later" hedge, per CLAUDE.md Rule 2. The
  provenance field (§3.1) honestly admits upstream model-assisted structure (D79 titles/roles)
  without weakening the render-path rule.
- **H4 — conditional header: Accept with two spec nits** (S1, S2 below): the decision
  procedure's rule 2 references an input outside the declared function signature, and the α
  dominance guard is stated outside the ordered precedence.
- **H5 — Slack body_only: Accept.** Correctly provisional, eval-gated, with a named fallback.
- **H6 — summaries out of embed + grounding: Accept in decision text; propagation Missing**
  in `e0_files_design.md` (M2). The D79 supersession note in `decisions.md` is well done —
  historical Wave-3 text preserved and explicitly marked non-default.
- **H7 — durable work graph: Accept with amendment** — the logical shape is right; its
  `processing_state` mapping is unbound (S3).
- **H8 — design home + amendment surface: Accept with amendment** — see checklist item 9.
- **H9 — typed grounding: Accept.** The provenance-allowlist table is the correct load-bearing
  fix, and the explicit sentence that bare removal is incorrect will prevent the naive
  implementation. Residuals are E2-doc consistency, not design (M4, S5).

---

## 4. Mechanism deep-dives

### 4.1 Embedding-input policy (policy §4)

Sound core: content-addressed policy artifact (§4.1) covering counter identity, normalization,
precedence, and null rendering — changing any of it is a new version, which is the right
freeze boundary. Header escaping (§4.4) and version-pinned mutable titles are good catches
that were absent from the pre-binding proposal. Numbers are labeled starting points, per
CLAUDE.md. Three defects:

1. **Rule 2's condition is outside the declared function signature** (S1). §4.3 declares the
   policy "a **total pure function** of `(location_facts, body, document_stats)`", but rule 2
   branches on "useful message scalars **are projected for filters**" — deployment/recipe
   configuration, which is none of the three inputs. As written, flipping a retrieval-side
   scalar projection on would change embedding *mode* for message atoms — a re-embed trigger
   that appears nowhere in §4.5's migration table, and a coupling between query config and
   embedding text that the interchangeability checklist (§8.4's spirit) exists to prevent.
   Fix: freeze the scalar-availability assumption *into the policy artifact* (e.g. a
   per-`source_shape` boolean constant in the version), so the function stays pure and a
   projection change is a deliberate policy-version bump.
2. **The α dominance guard has no place in the precedence** (S2). §4.3 lists six ordered
   rules; α ("if header length ≥ α·body length, force compact or body_only") appears only in
   the starting-hypotheses paragraph. A total function needs the guard's application point
   (post-check after rules 4–5? which of "compact or body_only" wins?) defined, or the same
   inputs can produce two outcomes across implementations of the same policy version.
3. **Vector-reuse rule contradicts its own migration table** (M3). §4.5 binds reuse to
   `embedding_text_hash ∧ policy_version ∧ embedder_generation`, but the migration row above
   it says a policy-version change re-renders and embeds "**iff hash changes** under same
   embedder generation." A policy bump that renders byte-identical text (hash unchanged)
   satisfies the migration row (no re-embed) while violating the reuse rule (version
   mismatch → prior vector unusable). Both cannot hold. The mathematically honest key is
   `embedding_text_hash + embedder_generation` (identical bytes → identical vector), with
   `policy_version` carried as a stamp; alternatively keep the triple key and change the
   migration row to "embed iff hash changes; else re-stamp policy version on the existing
   vector." Either is fine; pick one in both `e1_embedding_input_policy.md` §4.5 and
   `e1_chunks_design.md` §7 (which inherits the triple rule verbatim), and align D80 clause 6.

Also worth one sentence in §4.4: `model_derived` upstream facts (e.g. an auto-generated
document title) *may* legitimately flow into headers — the no-LLM rule is about the render
path, and §3.3's provenance filter already keeps them out of the grounding union — but the
doc never says so explicitly, and an implementer could read "no default LLM" as excluding them
from headers entirely.

### 4.2 P1 scalars (policy §5, retrieval amendment)

Right discipline throughout: small universal set (`source_kind`, `source_shape`,
`section_role`, embedder generation id), source-specific refs only with recipe operators,
opaque stable ids over display names, Postgres as full-snapshot authority, D74 purge. The
retrieval amendment's sentence "Scalars without filter support do not satisfy the contract"
(`retrieval_design.md` §3 amendment) is exactly the anti-landfill enforcement the first review
asked for. One open point: **policy §5.5 orders the claims-channel choice made "in retrieval
design when implementing; do not leave it implicit" — and the retrieval design's D80 amendment
does not make it** (S4). So the inherit-on-claim-rows vs join-chunk→doc choice is currently
left exactly where §5.5 forbids leaving it. This blocks implementing message-filtered claim
search ("claims from #eng since May") and is a Rule-2 hedge in effect if left as-is.

### 4.3 Work graph and durability (policy §6, orchestration)

The shape is correct and directly answers the geometric-failure finding that started this
program: pure prepare stamped per chunk inside a document/representation transaction (no
per-pure-fn ledger rows — the O10 rejection honored), durable capability-bounded embed
batches, unique cost-ledger `call_key`s per batch (the silent `ON CONFLICT` discard trap from
the analysis, closed), poison isolation, upsert-P1-then-stamp-PG ordering, and a readiness
barrier that admits typed skips. Fault injection is spike 10.4. What is **not** bound is the
mapping onto the ledger this repo actually runs on (S3): what `target_kind`/`target_id` grain
the *prepare* and *embed* stages claim in `processing_state`; which processing row anchors a
batch's `call_key`s; and what mechanism implements the readiness barrier — i.e. who enqueues
E2 when the last batch lands (the fan-in question the O10 analysis named as the hard part).
The likely intent — one document/representation-grain embed row with per-chunk durable stamps
inside it, chain rule fires on row completion — is consistent with everything written, but it
is an inference, not a contract. `orchestration_design.md` §1 declares itself the owner of
"what none of them owns" and currently only carries a pointer to policy §6; given this
program exists because the previous execution shape was wrong, the ledger mapping should be
written down, not re-derived by the implementer.

### 4.4 E2 grounding (e2 §3.3 amendment, policy §3.3)

The typed replacement is the correct fix and is well-guarded: provenance allowlist
(`source | connector | deterministic_derived`), free-form headers out as blobs, ordinals and
mode labels and `model_derived` orientation out, structured elements "parallel to
`document_header`." Two residuals:

- **The bundle (prompt-side) contract is not restated** (M4). The D80 amendment governs union
  *membership*, but `e2_e3_claims_relations_design.md` §3.1's bundle table still lists "The
  chunk's E1 context prefix — the compact 'where this sits' sentence E1 already wrote"; the §1
  diagram still says "a context prefix per chunk"; spike 5 (§7) still asks to "pin the E1
  context prefix's length." Under D80 nothing writes that sentence. What the extractor
  *receives* for location (the typed elements rendered how? the optional bounded header when
  present?) is specified nowhere — yet decontextualization quality ("Alice said X in #eng")
  depends on it, and the union's definition ("source-derived **bundle elements**") makes
  bundle membership load-bearing for grounding. The e2 doc needs its §1/§3.1/spike-5 text
  reconciled and one paragraph on the D80 bundle location element.
- **No version boundary named for the gate change** (S5). The union change alters what the
  layer-2 gate accepts. Past amendments in this doc bumped `extractor_version` for bundle
  changes (D79 consequences); D80's amendment names no bump. Stored claims are immutable and
  keep their generation's acceptance — fine — but without a version stamp on the gate change,
  cross-generation grounding metrics (e.g. the #161 loss ledger) can't attribute
  rejection-rate shifts. One sentence fixes it.

### 4.5 Migrations (policy §4.5, §7)

The trigger table is the executable artifact the first review asked for, and the
generation-safe P1 cutover (§7: new generation, atomic query-generation cutover, never
in-place upsert as the sole story) lands Codex's point precisely. Scalar-only changes
correctly avoid re-embeds. The legacy cutover (existing corpora with LLM prefixes) is
implicitly covered — new policy version ⇒ re-render ⇒ hash change ⇒ re-embed under a new
generation, column alias kept during transition (§2) — and honestly declared a multi-PR
program in D80's consequences. Residuals are M3 (the reuse-key inconsistency above) and the
schema-design contradiction (M1), which is where an implementer of the migration would
actually look for the columns.

---

## 5. Gaps that still block implementation

1. **`postgres_schema_design.md` binds the superseded architecture** (M1). §7's `chunks` DDL
   carries `context_prefix` ("generated 'where this sits' sentence (E1)"), `prefixer_version`
   → `context_prefixer` (still in the §2 component enum, line ~144), and a lone
   `embedding_version` — no `source_shape`, no location-facts snapshot ref, no bounded-header
   column, no `embedding_text_hash`, no `embedding_input_policy_version`, no embedder
   generation stamp. §16's E1 flow narrative (line ~2538) likewise. This now contradicts
   `e1_chunks_design.md` §8 (amended) and policy §7. Anyone implementing the schema PR from
   the schema design builds the wrong table.
2. **`e0_files_design.md` §4.1 still binds the summary→prefix channel** (M2): "they feed E1
   context prefixes" (line ~216), the no-fan-out corollary, and the "accepted second-order
   channel" block (lines ~415–435) all read as live design. The supersession lives only in
   `decisions.md`; the design doc a cold reader opens says the opposite. Needs a dated D80
   amendment note in place (history preserved, per house style).
3. **E2 bundle location contract** (M4, §4.4 above) — blocks the extractor prompt change.
4. **Work-graph ledger mapping** (S3, §4.3 above) — blocks the embed-graph PR from being
   written against a contract rather than an inference.
5. **Claims-channel scalar choice** (S4) — blocks message-filtered claim recipes.
6. **Policy input signature / α guard** (S1, S2) — blocks writing the policy module as the
   total pure function the design claims it is.

None requires new design work; all are bounded text amendments plus one decided choice (5).

---

## 6. Ranked findings

**Must fix before merge**

- **M1** — Amend `postgres_schema_design.md` (chunks DDL, component enum, §16 flow) to D80,
  or stamp those sections with an explicit "superseded by D80 / `e1_embedding_input_policy.md`
  §7" note. Binding-vs-binding contradiction.
- **M2** — Amend `e0_files_design.md` §4.1 summary-consumption text with the D80 supersession
  (summaries not default embedding-text inputs; second-order channel historical).
- **M3** — Reconcile the vector-reuse key with the policy-version migration row (policy §4.5,
  `e1_chunks_design.md` §7, D80 clause 6 — one consistent rule in all three).
- **M4** — Reconcile `e2_e3_claims_relations_design.md` §1 diagram, §3.1 bundle table, §7
  spike 5 with D80, and specify the bundle's location element under the new contract.

**Should fix**

- **S1** — Freeze rule 2's scalar-projection condition into the policy artifact (or restate
  the rule without it); as written the "pure total function" claim is false.
- **S2** — Place the α dominance guard in §4.3's ordered precedence and resolve
  "compact or body_only."
- **S3** — Bind the `processing_state` grain for prepare/embed, the batch `call_key`
  anchoring, and the readiness-barrier/fan-in mechanism (natural home:
  `orchestration_design.md` §2, which owns enqueue granularity).
- **S4** — Make the §5.5 claims-scalar choice in `retrieval_design.md` (inherit vs join).
- **S5** — Name the grounding-union change a gate/extractor version boundary in the E2 doc.
- **S6** — Update `plan/analysis/workers.md` rows 6–7 and §4.6: D80's consequences claim the
  inventory drops the per-chunk location LLM, but row 6 still reads "exists — F8 resolved by
  D63." Analysis, not binding — but `orchestration_design.md` names it *the* worker inventory,
  so it is load-bearing referenced content.
- **S7** — Fix policy §3.1's "see §6.3" cross-reference (anchors → should point at §4.4/§6.2).

**Fine / noted**

- `retrieval_design.md` line ~279 "generated context prefix" — stale wording only; the D80
  amendment and the P1 clarification above it are correct.
- `website/docs/reference/api` still documents `context_prefix` — **correct** under D66
  (docs describe what ships on `main`; the shipped code still has it). Becomes a same-PR docs
  obligation when implementation lands.
- Rule 6 sends short single-chunk documents to `body_only` (title signal absent from both
  vector and FTS text); defensible under the dominance rationale, but spike 10.2 should
  include this case explicitly.
- Section-role provenance is path-dependent under D79 (deterministic title rule vs bounded
  classifier) — §3.3's per-field provenance handles it correctly; a sentence noting it would
  help implementers.
- D63/D79 history handling (amend-in-place with dated notes, nothing erased) is exemplary.

---

## 7. Executive summary

1. **Accept as binding with required amendments.** The policy design itself is complete,
   self-contained, and correct; all nine prior must-fixes landed in it (one Partial).
2. The residual risk is **propagation, not architecture**: `postgres_schema_design.md` still
   ships DDL for the superseded LLM prefix, and `e0_files_design.md` still binds the
   summary→prefix channel — two binding docs now contradict D80 (M1, M2).
3. One real internal inconsistency: the vector-reuse triple key vs the "embed iff hash
   changes" policy-version migration row cannot both hold (M3).
4. The E2 doc amends the grounding union but leaves the bundle table, diagram, and a spike
   describing the dead LLM prefix — and never says what the extractor now *sees* for
   location (M4).
5. Smaller contract gaps: rule 2 depends on an undeclared input, the α guard sits outside
   the precedence, the work-graph→`processing_state` mapping is inferred not bound, and the
   claims-scalar choice §5.5 mandates was not made (S1–S4).
6. Everything above is text-sized amendment work; nothing reopens H1–H9, and nothing in the
   fixed owner constraints (conventional-only, no contextual, no hotfix-as-architecture,
   Slack + long-form) is violated by the binding text.
