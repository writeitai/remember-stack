# E2 / E3 — Claim Extraction and Relation Normalization (Design)

How the system turns a chunk of source text into **claims** (atomic, standalone, verifiable
assertions) and then into **relations** (the distinct facts those claims are evidence for). This is
the cost center and the quality bottleneck of plane E, so the design is opinionated about *what* to
extract and *how* to keep it faithful. Decisions: **D31–D35** (this layer), building on D2, D4, D7,
D12, D17–D19. Full research + evidence: `plan/analysis/claimify_research/SYNTHESIS.md`.

> **Amended 2026-08-26 (D95–D96).** E3 `EntityRef` is a **name** only; no class.
> Domain/range type gates are withdrawn. Bare-noun eligibility, aliases, untyped
> `works_for`:
> [`entity_identity_and_retrieval_design.md`](entity_identity_and_retrieval_design.md).
> Claimify (E2) is unchanged.

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

- **Understandability fails.** In isolation the model cannot know what *It* is or which valid-time
  bounds *last year* denotes, so it emits `"It launched last year in three markets"` — a claim whose
  entity and valid-time cannot be resolved downstream. With the context bundle, the entity is
  decontextualized in `claim_text`; the relative wording stays there, while its anchored resolution
  lands only in the structured valid-time fields.
- **Faithfulness is mis-aimed.** A *verbatim-quote* requirement rewards copying surface text, which is
  the opposite of making a claim standalone — and it has no opinion about whether `"The team considers
  it a runaway success"` (an opinion, not a checkable fact) should be a claim at all.

E2 fixes both by giving the extractor **context** and a **three-stage job** (D31), and by replacing
verbatim-quoting with **provenance + entailment** grounding (D32).

## 3. E2 — claim extraction

### 3.1 What the extraction call sees (the context bundle)

The extractor never sees a bare chunk. For each target chunk it receives a small, ordered bundle
(D31), **as amended by D80**:

| Element | Why it earns its tokens |
|---|---|
| **Document header** — title, date, source, language | resolves "this report", "the company", and absolute time for "last year" |
| **PageIndex section path + summary** | orientation only — *Results* vs *References*; summaries are **not** grounding sources (D79/D80) |
| **Typed location elements** (D80) | closed `LocationElement` list from E1 prepare (titles, channel/author/time when present); **not** a free-form E1 embedding header |
| **±1 (then ±2) neighbour chunks**, same section | antecedents for pronouns / partial names — section-parent + offsets, **same scope only** |
| **Known entity hints** | canonical names already on the chunk, as *hints* (permission to resolve, not to invent) |

**Not in the bundle:** free-form `location_header` / legacy `context_prefix` prose (D80). Location
for decontextualization comes from **typed location elements** + header fields + body/neighbours.

Cost is controlled by sharing one cached per-document header/orientation block across that
document's chunks where prompt caching applies. (Open question: very short sources — chat turns,
tool output — don't reach the prompt-cache minimum; see §7.)

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

2. **Decontextualization — make it stand alone.** Resolve every pronoun, partial name, and acronym
   **using the bundle, never outside knowledge**, and add the **minimum** context needed
   — over-stuffing both bloats the claim and risks asserting something the source didn't. Coreference
   is handled right here, in the same call (D19): no claim leaves E2 with a dangling pronoun. The
   discipline that makes this safe: **if a careful reader could not pick one interpretation from the
   bundle, drop the candidate** rather than guess. In the example, the neighbours name *Project Atlas*
   so "It launched last year" becomes "Project Atlas launched last year." The relative wording remains
   in claim text; its absolute resolution is structured valid-time, described below.

3. **Decomposition — split into atoms.** Break the disambiguated sentence into the simplest standalone
   claims, preserving attribution ("*X said* Y" stays attributed, it does not become a bare "Y"). The
   example yields two: `"Project Atlas launched last year."` (with the resolved interval only in its
   structured valid-time fields) and `"Project Atlas launched in three markets."`

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
  bundle element it came from (`neighbour` / `header` / **`location` with `element_id`** — not a
  free-form prefix blob; D80).

**Amendment (2026-07-27, issue #146):** the fused Claimify call also emits optional D41
source-asserted valid-time as *nullable typed scalars only* (no free-form objects — strict-schema
constraint from #145): `valid_kind` (`proposition_validity|event_time|measurement_period|effective_period`),
`valid_from_iso` / `valid_until_iso` (ISO-8601 date or datetime strings), and `valid_precision`
(`unknown|instant|day|month|quarter|year|open`). Relative dates resolve from bundle timestamps only;
the #158 amendment below specifies their structured-only output. E2 parses them deterministically into
`claims.claim_valid_*`; a malformed string falls back to unknown/null for the temporal fields
without rejecting the claim. Most claims have no stated world-time and leave these null/unknown.

**Amendment (2026-07-29, issue #158):** relative temporal expressions are resolved against an
absolute timestamp in the document header whenever that arithmetic supports an honest interval.
The computed date goes only into `valid_from_iso` / `valid_until_iso`; `claim_text` keeps the relative
phrase as the source spoke it. For example, with a 2023-05-08 header, "visited yesterday" keeps that
wording and emits the day bounds 2023-05-07, while "painted last year" emits the year bounds
2022-01-01 through 2022-12-31 with `valid_precision=year`. With no absolute in-document anchor, or
when a vague phrase cannot fit the available precision vocabulary without invention, E2 omits the
structured time. D32 layer 2 does not gate these structured fields: its membership union applies only
to text in `added_context`. The evidence-row builder used by `claims_verbatim`, claim hydration, and
`explain` now returns `claim_valid_from` and `claim_valid_until`. A #158 non-goal is surfacing
`valid_precision` or `valid_kind` on `EvidenceResult`: it exposes only `claim_valid_from` and
`claim_valid_until`. Precision is inferable from the bounds for these cases (equal ends = day;
calendar-year span = year); surfacing the enums is a possible follow-up, not part of #158.

**Amendment (2026-07-29, union grounding — historical):** layer-2 membership was checked against
the TARGET CHUNK slice, deterministic document header, same-section neighbours, and the stored
context prefix. The model-emitted `added_context.source_kind` is advisory. That fix addressed
GLM-5.2 mislabel deaths in the #161 loss ledger. **Section summaries remained excluded.**

**Normative union (D80, 2026-08-03 — replaces free-form prefix in the union):** membership is
against the **union of**:

1. TARGET CHUNK body slice  
2. Deterministic document header (source-derived fields)  
3. Same-section neighbour chunk bodies  
4. **Typed `LocationElement` texts** (`e1_embedding_input_policy.md` §3.3)

**Out of union:** free-form location headers / legacy `context_prefix` prose; section summaries;
model_derived orientation; policy labels; pure ordinals. Wrong `added_context` tags cannot reject
text that exists elsewhere in the union (token-tolerant rule below unchanged).

**Amendment (2026-07-29, token-tolerant union grounding):** the membership unit is now a token,
not the whole added connective phrase. E2 tokenizes Unicode words and punctuation, splitting a
possessive such as `Caroline's` into `caroline` and `'s`. A token passes when the same token occurs
case-insensitively at a word boundary in any source-derived union element, or when it belongs to this
closed functional allowlist:

- attribution scaffolding: `said`, `says`, `saying`, `asked`, `asks`, `told`, `tells`, `mentioned`,
  `mentions`, `wrote`, `writes`, `according`;
- pure function words: `that`, `the`, `a`, `an`, `of`, `to`, `in`, `on`, `at`, `and`, `or`, `is`,
  `was`, `were`, `be`, `been`, `she`, `he`, `they`, `her`, `his`, `their`, `it`, `its`, `this`,
  `these`, `those`, `with`, `for`, `as`, `by`, `from`;
- punctuation: `,`, `.`, `:`, `;`, straight or curly single/double quote tokens, and `'s`.

Empty or whitespace-only additions add no information and are skipped. Numeric tokens are **never**
allowlisted: `in 2022` fails when `2022` is absent from the union and passes only when `2022` occurs
there. All proper names and other content nouns, verbs, and adjectives obey the same source-only
rule. Thus `Melanie said, ` passes against a `Melanie:` speaker label (`said` and punctuation are
functional; `Melanie` has a word-boundary match), while `in Paris` fails when `Paris` is absent.
Every content token of every accepted addition therefore remains traceable verbatim, ignoring case,
to source-derived bundle text; token tolerance creates no outside-knowledge channel.

The reason is a measured prompt/gate contradiction. The prompt requires attributed claims to stay
attributed and pronouns/possessives to be decontextualized, but whole-string membership rejected the
resulting grammar because source transcripts use forms such as speaker-label colons rather than
`X said`. In the conv-26 GLM-5.2 E2 07h ledger, 144 claims were grounding-rejected and this mandated
scaffolding was the dominant class (about 40–60%); 13 empty additions were rejected too. A gold claim
from `Melanie: Yeah, I painted that lake sunrise last year!` died solely because its addition was
`Melanie said, `. Failed additions still reject and drop the claim, and their
`grounding_rejected.edit_detail` now includes `failed_tokens` for diagnosis.

Acceptance layers four checks, cheapest first:

1. **Anchor** (deterministic): the `source_span` must be a real, in-bounds slice of the target chunk —
   a check the model cannot talk its way past.
2. **Window-membership** (deterministic): tokenize every non-empty addition; every content and numeric
   token must occur in the source-derived union above, while only the closed functional allowlist may
   supply absent scaffolding. The attribution tag is advisory. A claim that invents `in San
   Francisco` with neither content token in the union is rejected and records `san` and `francisco`
   as its failed tokens.
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

So in the example, `"Project Atlas launched last year"` is accepted: its anchor is the verbatim "It
launched last year", and "Project Atlas" (→ neighbour) is the only `added_context` entry. Both content
tokens occur in the source-derived union. The resolved bounds are emitted only in the structured
valid-time fields and do not enter the membership gate. The attributed stance is grounded separately.

### 3.4 Nothing is silently lost (D33, D35)

Two safeguards keep aggressive Selection safe:

- **A decision ledger (D33).** Every Selection drop, every decontextualization edit, and every
  Claimify-stage loss is written to an append-only, version-stamped `claim_extraction_decisions`
  table. A better prompt can later re-examine *only the drops*; a rebuild reads stored claims +
  decisions and never re-calls the model (the LLM rungs are replay-from-storage, like any
  non-deterministic stage — D7); the per-chunk worker is idempotent on content-hash + extractor
  version (D12).
- **A recall envelope (D35).** Selection biases toward KEEP when unsure; **never-drop classes**
  (quantities, dates, named-entity + predicate, change-of-state language) are protected even if phrased
  opinionatedly; a low-confidence `kept_flagged` outcome marks-for-review instead of hard-deleting; and
  planted rare-fact canaries fail CI if Selection drops them. Drop-rates are tuned against **per-fact**
  loss, never a corpus average — a uniquely-attested fact has no second copy to fall back on.

**Amendment (2026-07-27, issue #161):** Selection keep/keep_flagged is not the end of the story.
Between a keep and an accepted `claims` row sit Claimify (the fused decontextualize+decompose call)
and the deterministic D32 grounding gates. Without ledger rows for those stages, a keep that never
lands a claim is indistinguishable from a keep that produced a claim the gate rejected — and plain
(unflagged) keeps that die are completely invisible. The D33 transcript therefore also records:

| `decision_type` | When written | `source_span` | `edit_detail` |
|---|---|---|---|
| `claimify_omitted` | A kept Selection span for which Claimify returned **no claim at all** (the model simply skipped it). One row per dead keep. | The Selection span. | null |
| `grounding_rejected` | A Claimify-returned claim rejected by a D32 gate. One row per rejected claim. | The claim's returned `source_span` (even if not findable in the chunk). | `{"gate": "span_not_found" \| "outside_kept_ranges" \| "added_context_unverified", "claim_span": <truncated>}`; for `added_context_unverified` also `{"kind": ..., "text": <truncated>, "searched_elements": [...], "failed_tokens": [...]}`. |

**Invariant — every kept span is accounted for end-to-end.** Two independent rules (revised
2026-07-27 after review — one keep can decompose into several returned claims with mixed fates,
so "exactly one category per keep" was wrong):

1. **Every Claimify-returned claim** independently ends either **accepted** (a `claims` row, plus
   any `decontext_edit` / `selection_keep_flagged` pairing that already applied) or
   **`grounding_rejected`** (one row naming which gate fired). A mixed outcome — same keep, one
   claim accepted, another rejected — records both and is not an omission.
2. **Every keep or keep_flagged span with no attributable returned claim** gets exactly one
   `claimify_omitted` row. Attribution is **anchored-range overlap only** (the claim's resolved
   char range overlaps the keep's) — text containment is deliberately excluded so one claim
   cannot suppress omission rows for unrelated keeps that merely share text. Two conservative
   consequences: a returned claim whose span anchors nowhere is an **orphan rejection** (its
   `grounding_rejected` row stands; it suppresses no omission), and a Selection span that is not
   verbatim-findable can never be marked "tried," so it always gets its omission row — the case
   that previously vanished with no trace.

Cross-model extraction comparisons can then show *why* a stronger model lands more claims (fewer
omissions vs fewer gate rejections) instead of only *that* it does. On D56 chunk reuse with zero
attached claims, the prior occurrence's transcript is copied forward verbatim — the synthetic
`no_info` marker is written only when the prior transcript is itself empty, so reuse never
rewrites a real loss reason.

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
  entity's observations by semantic similarity using its versioned write-path rank cache. The adjudicator decides supersede (cap the
  prior window) / contradict (both stand, shared `contradiction_group`) / evidence / new, and **fails
  safe to coexist** when unsure — so a both-stand figure is never silently overwritten without any typed
  attribute vocabulary. Full design: `observations_design.md`.

## 6. End-to-end, in one example

> Source chunk (inside a *Results* section of a 2025 product memo): *"It launched last year in three
> markets. The team considers it a runaway success."* Neighbour text names **Project Atlas**.

| Stage | What happens |
|---|---|
| **E1** | chunk + typed location elements (document title, section title *Results*, …) — not free-form prefix prose (D80) |
| **E2 Selection** | keep "launched last year in three markets"; **keep** "The team considers it a runaway success" as the team's attributed stance (D59 — a bare, holderless version would drop → ledger) |
| **E2 Decontextualize** | "It"→Project Atlas (neighbour), while "last year" stays source-faithful in claim text → *"Project Atlas launched last year in three markets"* |
| **E2 Decompose** | `"Project Atlas launched last year."` (against the 2025 header, emits `valid_kind=event_time`, 2024 `valid_from_iso`/`valid_until_iso` bounds, `valid_precision=year` — D41; amendment 2026-07-29 / #158) + `"Project Atlas launched in three markets."` |
| **E2 Grounding** | each accepted: anchor span present, textual additions trace to the bundle, entailed; structured valid-time does not enter D32's `added_context` membership gate |
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
5. **Typed location elements (D80)** — measure decontextualization quality with structured
   location only (no free-form header); pin compact header length on the embed path separately.
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
