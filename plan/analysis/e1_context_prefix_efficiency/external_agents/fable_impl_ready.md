# Fable — D80 implementer-readiness review (round 2)

**Reviewer:** Fable (external agent pass, 2026-08-03, after commit 33a66a9)
**Scope:** `e1_embedding_input_policy.md` §3.2–3.3, §4.5, §5, §6–7;
`orchestration_design.md` § embed_chunk durability; `e2_e3_claims_relations_design.md`
§3.1 + D80 grounding union; `retrieval_design.md` D80 amendments;
`postgres_schema_design.md` `chunks` stamps read for cross-check.
**Bar:** an implementer can start without inventing contracts. Review only; no edits made.

---

## 1. Verdict

**Ready to implement.**

Both round-1 must-fixes are closed with explicit, mutually consistent bindings across
all four docs. No remaining item forces an implementer to invent a contract or blocks
a first PR. Three non-blocking wording nits are listed in §4.

---

## 2. Prior must-fixes — closure check

### MF1 — `passage_generation` grain confusion → **Closed**

The one-definition ask landed as an explicit three-grain table plus pinned key shapes,
and every load-bearing statement now uses the tuple forms directly:

- E1 §4.5: three grains (`embedder_generation` / `policy_generation` / per-chunk
  embedding identity), **active cutover pointer = `(policy_generation,
  embedder_generation)` — "not a per-chunk hash"**, and **P1 row key (v1) =
  `(chunk_id, policy_generation, embedder_generation)`** with `embedding_text_hash`
  stored on the row as a verify/attestation stamp, never in the pointer
  (`e1_embedding_input_policy.md:243–259`).
- Orchestration embed_chunk rule 5 now keys P1 by the same triple with stored hash,
  and the crash-recovery rule ("triple exists and hash matches ⇒ no provider call")
  is stated against that key (`orchestration_design.md:168–172`).
- Retrieval hydration confirms against "the active `(policy_generation,
  embedder_generation)` pointer" (`retrieval_design.md:150`).
- E1 §5.2 filter scalars are now **both** halves of the pair (fixes the
  embedder-only filter ambiguity), and §7 repeats key + pointer verbatim.
- PG schema: `chunks.policy_generation` replaces the old composite-id column; the
  undefined "dual-generation table" mention is gone; the E1 doc's dual-generation
  cutover paragraph now says "versioned per-chunk embedding records (or an
  equivalent generation manifest)", which the triple-keyed P1 rows satisfy.

Residual: the retired *word* `passage_generation` still appears twice in E1 §4.5
(lines 270, 282) — see nit N1. Both occurrences are unambiguous in context (the
definitions sit directly above them), so this is naming hygiene, not grain confusion.

### MF2 — P1 text column layout undecided → **Closed**

Bound exactly along the recommended resolution, and labeled as non-optional:

- E1 §7: "**P1 text column (bound decision — no implementer choice): store the
  normalized body only**"; header never in the text/BM25 column; vectors still
  computed over embedding text; header retained on the PG stamp and returned
  **separately** on hydration (`e1_embedding_input_policy.md:370–376`).
- Retrieval repeats it at the consumption site: "P1 text column is normalized body
  only — not header+body", header returned separately, `embedding_text_hash`
  verified against the PG stamp (`retrieval_design.md:150–154`).
- The round-1 tension source "full embedding text lives in P1" is gone; §11 lists
  "storing full embedding text in PG for every chunk" as a non-goal.

Residual: one stale overview sentence in E1 §2 — see nit N2.

---

## 3. Invent-or-block items

**None.** Choices an implementer meets on day one all have closed defaults:
connector-less start ("headers/filters use only structure-derived facts" until a
connector implements §3.2), the closed `LocationElement` record + kind/provenance
allowlists (§3.3, mirrored by the E2 §3.3 normative union), the total ordered mode
function with a bound provisional default for short message atoms (§4.3), the
claims-channel no-inheritance/join decision (§5.5, repeated in retrieval), the
embed_chunk durability contract (claiming row, batch bounds, `call_key` format,
poison split, cross-store crash order, `empty_body` typed skip), and the
generation-safe cutover story. Deliberately unmeasured knobs (`T_short`, `H_max`, α,
batch size, counter identity) are version-scoped inside the policy artifact, so any
initial pick is valid and swappable — that is the repo's stated numbers discipline,
not a gap.

---

## 4. Non-blocking nits (wording only; no contract impact)

- **N1** — E1 §4.5 still uses the retired name twice: "attestation into the new
  `passage_generation`" (line 270; should read `policy_generation`, since that is
  the half that changed) and "active passage_generation pointer" (line 282; the doc's
  own name for it is the "active cutover pointer" pair). One rename, or a one-line
  "the active pair is also called the passage generation" gloss.
- **N2** — E1 §2 (line 52) retains pre-fix phrasing: "P1 passage search may store
  embedding text for the vector channel; … (policy decides)". §7 has since bound the
  layout with "no implementer choice". Tighten to "vectors are computed over
  embedding text; the P1 text column stores body only (§7)" so a first-time reader
  never sees an apparent contradiction.
- **N3** — `postgres_schema_design.md:1297–1300` routes the embedder half of the P1
  key through a column marked LEGACY (`embedding_version`) via a comment. Works, but
  a first-class `embedder_generation` stamp (or renaming the comment) would keep the
  schema self-describing.

---

## 5. Cross-doc consistency spot-checks (all pass)

- E1 §3.3 union table ⇄ E2 §3.3 normative union: identical four members, identical
  exclusions (free-form header/prefix, summaries, model_derived, policy labels,
  ordinals); `added_context` location tags point at `element_id` with membership
  still token-in-union.
- E2 §3.1 bundle lists typed location elements and bans the free-form header;
  provenance allowlist structurally excludes `model_derived`.
- Orchestration `call_key` `embed_chunks:{sorted_first_chunk_id}:{count}` is unique
  per disjoint batch (disjoint sets cannot share a minimum element) and stable under
  poison-split retries.
- Readiness barrier wording matches across E1 §6.2 and orchestration rule 6
  (active pair + closed typed skips; mixed generations not ready).

---

## 6. Summary

Round 1's two invent-or-block items — the `passage_generation` grain collision and
the undecided P1 text layout — are both closed with explicit tuples repeated at every
consumption site (E1, orchestration, retrieval, PG schema). What remains is three
lines of wording hygiene. An implementer can start the E1 prepare/embed path, the P1
schema, the E2 wire, and the retrieval filters today without inventing anything.

**Verdict: Ready to implement.**
