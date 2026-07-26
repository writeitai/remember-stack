# E2 / E3 — Claim Extraction and Relation Normalization (Design)

How the system turns a chunk of source text into **claims** (atomic, standalone, verifiable
assertions) and then into **relations** (the distinct facts those claims are evidence for). This is
the cost center and the quality bottleneck of plane E, so the design is opinionated about *what* to
extract and *how* to keep it faithful. Decisions: **D31–D35** (this layer), building on D2, D4, D7,
D12, D17–D19. Full research + evidence: `plan/analysis/claimify_research/SYNTHESIS.md`.

## 1. Where this sits

```
E0 ─────────► E1 ──────────► E2 ───────────────► E3
files         chunks         claims              relations
(Markdown,    (semchunk +    (Claimify-staged:   ( (subject,predicate,object) facts;
 PageIndex     a context      Selection →         entity resolution T0–T4;
 hierarchy +   prefix per     decontextualize →   supersession; evidence_count )
 summaries)    chunk; embed)  decompose; coref
                              in-call)
```

Every document that survives chunking goes all the way through — **there is no pre-extraction "value
gate"** deciding what is worth processing (§4 explains why). E0 and E1 are covered elsewhere; this
document is E2 and E3.

## 2. The problem E2 has to solve

A claim is only useful if a reader (human or agent) can understand it **without going back to the
source**, and only trustworthy if it is **actually supported** by that source. The obvious approach —
"show the model one chunk, ask it to extract every fact, and require each fact to be a verbatim quote"
— fails both tests at once. Take a chunk that reads:

> *"It launched last year in three markets. The team considers it a runaway success."*

- **Understandability fails.** In isolation the model cannot know what *It* is or when *last year*
  was, so it emits `"It launched last year in three markets"` — a claim no downstream step can use
  (you can't resolve an entity called "It" or a date called "last year").
- **Faithfulness is mis-aimed.** A *verbatim-quote* requirement rewards copying surface text, which is
  the opposite of making a claim standalone — and it has no opinion about whether `"The team considers
  it a runaway success"` (an opinion, not a checkable fact) should be a claim at all.

E2 fixes both by giving the extractor **context** and a **three-stage job** (D31), and by replacing
verbatim-quoting with **provenance + entailment** grounding (D32).

## 3. E2 — claim extraction

### 3.1 What the extraction call sees (the context bundle)

The extractor never sees a bare chunk. For each target chunk it receives a small, ordered bundle
(D31):

| Element | Why it earns its tokens |
|---|---|
| **Document header** — title, date, source, language | resolves "this report", "the company", and absolute time for "last year" |
| **PageIndex section path + summary** | tells the model it is inside *Results* vs *References*; makes intro/conclusion and list-item-without-preamble decidable |
| **The chunk's E1 context prefix** | the compact "where this sits" sentence E1 already wrote |
| **±1 (then ±2) neighbour chunks**, same section | the antecedents for pronouns / partial names — fetched for free from the chunk's section-parent + offsets, **same scope only** |
| **Known entity hints** | canonical names already on the chunk, as *hints* (permission to resolve, not to invent) |

Cost is controlled by sharing one cached per-document prefix across that document's chunks. (Open
question: very short sources — chat turns, tool output — don't reach the prompt-cache minimum; see §7.)

### 3.2 The three jobs, in one call's reasoning

Over that bundle the model does three things, in order (the "Claimify" shape). Each is a distinct
*decision*, not just a rewrite, and each is recorded:

1. **Selection — is this even a claim?** Keep statements that make a **specific, verifiable**
   proposition (a state, event, decision, quantity, policy, relationship). **Drop** *unattributed*
   opinions, advice, hypotheticals/speculation ("could lead to…"), generic truisms, questions,
   section intros/conclusions, and "we don't know X" statements. **An attributed stance is a
   keep (D59)**: "X said / believes / opposes Y" — including the document author's own voice,
   whose identity the bundle header carries — is a *verifiable proposition about X* (you can
   check the source and confirm X said it); it is kept as an attributed claim and later becomes
   a stance observation on the holder (§5). A stance whose holder cannot be resolved to an
   entity falls back to drop. If a sentence mixes verifiable and non-verifiable parts, **keep
   only the verifiable part**. In the example: `"launched last year in three markets"` is kept,
   and `"The team considers it a runaway success"` is *also* kept — as the attributed stance of
   the team (decontextualized to the canonical team/org entity); a bare "it is a runaway
   success" with no holder would drop. *(This stage is the single biggest quality lever — in
   the source research, removing it was the largest quality drop of any component.)*

2. **Decontextualization — make it stand alone.** Resolve every pronoun, partial name, acronym, and
   relative date **using the bundle, never outside knowledge**, and add the **minimum** context needed
   — over-stuffing both bloats the claim and risks asserting something the source didn't. Coreference
   is handled right here, in the same call (D19): no claim leaves E2 with a dangling pronoun. The
   discipline that makes this safe: **if a careful reader could not pick one interpretation from the
   bundle, drop the candidate** rather than guess. In the example, the neighbours name *Project Atlas*
   and the header dates the document to 2024, so "It launched last year" becomes "Project Atlas
   launched in 2024."

3. **Decomposition — split into atoms.** Break the disambiguated sentence into the simplest standalone
   claims, preserving attribution ("*X said* Y" stays attributed, it does not become a bare "Y"). The
   example yields two: `"Project Atlas launched in 2024."` and `"Project Atlas launched in three
   markets."`

**Two calls, not one (D31).** Selection is run as its own (optionally voted) call, then
decontextualization + decomposition + grounding run as a second fused call. Selection is split out
because it is the highest-leverage stage and because it carries the opposite instruction to
decontextualization ("ignore ambiguity" vs "resolve ambiguity"), which is cleaner to keep in separate
contexts. Collapsing to a single call is allowed only if an ablation shows it doesn't lose quality —
see §7. Running the literal three-calls-per-sentence form is *not* done; it is pure latency at scale.

### 3.3 Grounding — staying honest while rewriting (D32)

A decontextualized claim is a *rewrite*, so it can no longer be a verbatim substring of the source.
Grounding therefore stores **two things per claim** and accepts via **layered checks**:

- `claim_text` — the standalone assertion (what retrieval, E3, and reasoning use).
- `source_span` + character offsets — the verbatim slice the claim derives from (provenance / audit).
- `added_context[]` — each substring the model *added* during decontextualization, tagged with which
  bundle element it came from (neighbour / header / prefix).

**Amendment (2026-07-27, issue #146):** the fused Claimify call also emits optional D41
source-asserted valid-time as *nullable typed scalars only* (no free-form objects — strict-schema
constraint from #145): `valid_kind` (`proposition_validity|event_time|measurement_period|effective_period`),
`valid_from_iso` / `valid_until_iso` (ISO-8601 date or datetime strings), and `valid_precision`
(`unknown|instant|day|month|quarter|year|open`). Relative dates resolve from bundle timestamps only,
the same rule as decontextualization. E2 parses them deterministically into
`claims.claim_valid_*`; a malformed string falls back to unknown/null for the temporal fields
without rejecting the claim. Most claims have no stated world-time and leave these null/unknown.

Acceptance layers four checks, cheapest first:

1. **Anchor** (deterministic): the `source_span` must be a real, in-bounds slice of the target chunk —
   a check the model cannot talk its way past.
2. **Window-membership** (deterministic): every *added* substring must verbatim-exist in the bundle
   element it was attributed to. A claim that invents "in San Francisco" with no bundle source is
   rejected.
3. **Entailment self-verdict** (in-call, ~free): the model asserts the chunk + bundle entail the
   claim; includes the rule that "*X said* Y" entails "X said Y", not "Y".
4. **Sampled independent audit** (offline, not per-claim): a separate judge re-checks a sample, because
   self-grading is optimistic; only a borderline band ever escalates to a per-claim judge.
   **For media-derived documents the audit is modality-aware (D65):** the anchor (layer 1)
   proves the claim derives from the *representation* — the transcript or description in
   `document.md` — but it cannot prove the ASR heard or the VLM saw correctly, because the
   representation is itself model output. The auditor therefore follows the claim's source
   locator to the raw asset and checks against **the source modality**: listen to the
   referenced time interval, look at the referenced frame or region. Auditing only the derived
   Markdown would grade the converter against its own output. (Grounding is thus **two hops**
   for media: claim → `source_span`, exact and deterministic; span → source map → raw locator,
   at the converter's disclosed precision — `media_design.md` §4.)

So in the example, `"Project Atlas launched in 2024"` is accepted: its anchor is the verbatim "It
launched last year", and the additions "Project Atlas" (→ neighbour) and "2024" (→ header) both exist
in the bundle. The dropped opinion never reaches grounding.

### 3.4 Nothing is silently lost (D33, D35)

Two safeguards keep aggressive Selection safe:

- **A decision ledger (D33).** Every Selection drop and every decontextualization edit is written to an
  append-only, version-stamped `claim_extraction_decisions` table. A better prompt can later re-examine
  *only the drops*; a rebuild reads stored claims + decisions and never re-calls the model (the LLM
  rungs are replay-from-storage, like any non-deterministic stage — D7); the per-chunk worker is
  idempotent on content-hash + extractor version (D12).
- **A recall envelope (D35).** Selection biases toward KEEP when unsure; **never-drop classes**
  (quantities, dates, named-entity + predicate, change-of-state language) are protected even if phrased
  opinionatedly; a low-confidence `kept_flagged` outcome marks-for-review instead of hard-deleting; and
  planted rare-fact canaries fail CI if Selection drops them. Drop-rates are tuned against **per-fact**
  loss, never a corpus average — a uniquely-attested fact has no second copy to fall back on.

## 4. Why there is no value gate (the non-goal)

It is tempting to put a cheap "is this section even worth extracting?" gate *before* E2. We
deliberately do **not** (D25). The reasoning, in full, lives in the value-gate research
(`plan/analysis/value_gate_research/`); the short version:

- The only rung that actually discriminated *value* was a salience classifier that needs a labelled
  golden set that doesn't exist; the novelty rung was a corpus-scale similarity query — i.e. the gate's
  own worst risk was becoming a new expensive stage.
- The honest cost saving from skipping was ~1.5–2×, not the imagined 10×; the 10× lived entirely in an
  elaborate deferred-extraction subsystem (state tables, a promotion queue, a reconciler, four triggers)
  out of proportion to the lever.
- A pre-extraction skip is also where the worst correctness bug hides: skip the one section that
  supersedes an old fact and you serve a stale fact as current.

Instead, **junk-control happens where it is cheap and safe** (D34): E2 **Selection** drops low-value
statements in-call (§3.2), **D2** collapses duplicate facts into a single relation with an evidence
count (§5), and exact-duplicate inputs are a no-op re-ingest (idempotency, D12). The one real signal a
gate would have used — *this is a references section* — is **fed into Selection** (§3.1) instead of
thrown away as a binary skip; there it does more work.

*Documented add-back, not built:* if a corpus slice ever shows extraction cost is dominated by
structurally-skippable sections, the cheap fix is a single deterministic filter that keeps the
`references / bibliography / nav / boilerplate / legal` PageIndex node-types out of E2 — a metadata
branch, **not** a salience classifier and **not** a deferred-extraction machine.

## 5. E3 — claims become relations and observations

Claims are *what a source said*; relations are *the distinct facts*. E3 normalizes eligible claims
into `(subject, predicate, object)` records and is where redundancy and supersession are handled. The
internals (entity resolution, predicate registry, the supersession cascade) are designed in
`registries_design.md` (D17–D24); the pipeline view:

- **Normalize.** Each claim yields 0..n **relations** *and/or* **observations**. A two-entity claim
  like "Alice joined Acme" → a relation `(alice, works_for, acme)` via the governed predicate registry
  (D5, D18). A claim asserting a **value/property about one entity** — "Acme's headcount is 600",
  "Acme's FY2023 revenue was \$5M" — yields **no relation** but becomes an **observation** (D43): an
  entity-anchored, *untyped*, bi-temporal fact (`observations_design.md`; schema §9.A). So
  non-relational facts are no longer merely "kept as evidence" — they get first-class validity and
  supersession too. Time is still never a relation object or predicate (D18). (Unattributed opinion is still
  dropped at Selection — §3/§4 — and becomes neither a relation nor an observation. An
  **attributed stance** is kept (D59) and normalizes to a **stance observation on its holder**
  — "Bob opposes the pricing change", anchored on Bob, an effective state whose changes are
  ordinary supersession. The guard: the stance's *content* is never asserted as a world-fact —
  "X believes Y" yields the stance-about-X, never a fact about Y.) A claim's asserted world-time interval
  (D41) seeds the initial window of whatever it produces.
- **Resolve entities.** Subjects/objects are resolved to canonical entities through the tiered T0–T4
  cascade (D17). This is *why* decontextualization matters: "Project Atlas" resolves; "It" cannot. A
  claim with a dangling reference is dead weight here — which is the whole point of §3.2.
- **Collapse redundancy (D2, counting per D54).** The same fact asserted by 200 documents becomes
  **one** relation with **200 evidence rows**, not 200 edges — and `evidence_count` counts the
  **distinct document lineages with current-testimony support** (not evidence rows: re-extraction
  generations, document versions, and within-document repetition never inflate it — D54). It is
  then a free confidence/salience signal — the thing a value gate tried to compute up-front,
  obtained for free after the fact.
- **Adjudicate supersession (D3, D4).** New facts close the validity windows of the ones they replace,
  via `(entity_id, predicate)` blocking + a cheap-first cascade — adjudicated on **relations**, never on
  claims (claims stay immutable records of what was asserted).
- **Adjudicate observations (D43).** Non-relational facts supersede by the *same* cascade, but block on
  the **resolved entity** (an exact, exhaustive key) instead of `(entity, predicate)`, narrowing a hub
  entity's observations by semantic similarity (P1/Lance). The adjudicator decides supersede (cap the
  prior window) / contradict (both stand, shared `contradiction_group`) / evidence / new, and **fails
  safe to coexist** when unsure — so a both-stand figure is never silently overwritten without any typed
  attribute vocabulary. Full design: `observations_design.md`.

## 6. End-to-end, in one example

> Source chunk (inside a *Results* section of a 2024 product memo): *"It launched last year in three
> markets. The team considers it a runaway success."* Neighbour text names **Project Atlas**.

| Stage | What happens |
|---|---|
| **E1** | chunk + a context prefix ("…from the Results section of the Project Atlas 2024 memo…") |
| **E2 Selection** | keep "launched last year in three markets"; **keep** "The team considers it a runaway success" as the team's attributed stance (D59 — a bare, holderless version would drop → ledger) |
| **E2 Decontextualize** | "It"→Project Atlas (neighbour), "last year"→2024 (header) → *"Project Atlas launched in 2024 in three markets"* |
| **E2 Decompose** | `"Project Atlas launched in 2024."` (emits `valid_kind=event_time`, `valid_from_iso`/`valid_until_iso` for 2024's bounds, `valid_precision=year` — D41; amendment 2026-07-27 / #146) + `"Project Atlas launched in three markets."` |
| **E2 Grounding** | each accepted: anchor span present, additions trace to bundle, entailed; the date "2024" verbatim-exists in the bundle, so the asserted interval is grounded (D32) |
| **E3** | the stance claim becomes a **stance observation** on the team entity ("Acme's team considers Project Atlas a runaway success" — D59); neither decomposed launch claim yields a relation — "three markets" is a quantity and "2024" a date, neither a second entity (D2/D18); the temporal one carries `claim_valid_from = 2024` (**D41**), queryable as evidence. A later memo asserting 2023 makes a *second* immutable claim (`claim_valid_from = 2023`); with no relation to host them, **both stand** as evidence and there is no adjudicated supersession — the documented non-goal (`postgres_schema_design.md` §15). |

## 7. Decisions, and what is still a spike

**Decisions:** **D31** (Claimify-staged E2 over a context bundle, two calls), **D32** (layered,
dual-field grounding), **D33** (append-only versioned decision ledger), **D34** (E2 Selection is the
value filter — no pre-extraction gate), **D35** (Selection recall envelope), **D41** (claims carry an
immutable, source-asserted validity interval — extracted in-call here, grounded by window-membership;
asserted vs. adjudicated time). Foundations: D2, D3, D4, D5, D7, D12, D17–D19, D25.

**Spikes to clear before locking numbers** (full list in `claimify_research/SYNTHESIS.md` §4):

1. **One-call vs two-call** — measure on a golden slice before any collapse to a single call.
2. **Selection recall floor** — per-fact false-drop on a canary set; validate the never-drop classes.
3. **Grounding safety** — in-call self-verdict vs an independent judge; confirm the anchor +
   window-membership floor catches fabricated additions.
4. **Bundle cost per source-class** — the short-source tail breaks prompt-caching; decide a cheaper
   bundle (section path only, no neighbours) for chat/tool/git inputs.
5. **The E1 context prefix** — pin its length (or specify the E2 fallback when it is absent).
6. **Structured asserted-validity (D41)** — measure precision/recall of the extracted `claim_valid_*`
   interval on a golden slice; add a per-fact canary (D35) for window false-extraction; resolve
   fiscal-calendar expansion ("FY2023" ≠ calendar 2023 for off-calendar fiscal years — `precision`
   + the grounded source substring keep a wrong expansion auditable, not silently lossy). Recurrence
   ("every Q4") and un-datable anchor-events ("as of the merger") are out of the single-interval model;
   the documented upgrade is an expressivity child table, gated on measured demand.

## References

Research: `plan/analysis/claimify_research/SYNTHESIS.md` (+ questions C1–C8, verify/, the Codex
cross-check). Adjacent designs: `registries_design.md` (E3 internals, D17–D24), `overall_design.md`
(plane E), `concepts.md` (claims vs relations, bi-temporality). Decisions: `decisions.md`
(D31–D35 and the foundations above; D25 records why there is no value gate).
