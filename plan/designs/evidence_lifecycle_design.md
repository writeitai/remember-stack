# Evidence Lifecycle — Document Versions, Testimony Currency, and the Counting Rule (Design)

How the system stays truthful when **the evidence basis of a document changes** — either
because a better extractor re-processes it (re-extraction) or because the source itself was
edited (a watched Google Drive file, ingested hourly). Binding design for decisions
**D54–D56**, building on D2 (evidence collapse), D3 (claims immutable; supersession
relation-only), D12/D25 (content-hash idempotency), D33/D7 (ledgers; replay), D37 (E0
storage), D41 (asserted validity), D42 (origin), D43 (observations), D45–D51 (K triggers;
retrieval envelope). Research: `plan/analysis/evidence_lifecycle/` (internal + Codex parallel
analyses + SYNTHESIS — convergent on everything here except one mechanism, §9). Numbers are
starting points to measure, not committed constants (CLAUDE.md).

> **Reading this cold (CLAUDE.md Rule 1).** A **claim** is an immutable record of what a
> source asserted (E2) — never edited, never superseded. A **relation**/**observation** is an
> adjudicated fact the system currently holds (E3), linked to supporting claims by evidence rows;
> **`evidence_count`** caches how much support a fact has and feeds retrieval reranking (D9)
> and adjudication. A document's **`content_hash`** (sha256 of its
> bytes) is the idempotency key (D12); the **extractor version** stamps which prompt/model
> generation produced each claim (D33). Before this design, the system had no vocabulary for
> either "this document was re-extracted by a newer generation" or "this document was edited
> at its source" — both silently inflated `evidence_count` and polluted claim search.

## 1. The problem, in one example each

**Re-extraction (problem A).** Document D, extractor v1 → claim c1 ("Alice is VP at Acme") →
`relation_evidence(r, c1)`, count = 1. Extractor v2 ships (better Selection — the whole point
of versioning); D re-extracts; the same sentence yields c2 — a *new* claim_id, because claims
are immutable and append-only (correctly). E3's novelty gate sees the same fact → outcome
*evidence* → count = 2. One sentence, one document, and the system's headline confidence
signal doubled — and doubles again per generation, *only* for re-extracted documents, so
counts also stop being comparable across facts.

**Versioning (problem B).** A connector watches Google Drive hourly. A file is edited: new
bytes, new `content_hash`. The schema had no concept connecting the new upload to the old one
— it would ingest as an unrelated document, double-counting the unchanged 95 % of its content
(the versioning problem *is* the inflation problem at document grain), and paying full
conversion + extraction per edit.

**The shared root — three identities, kept apart (refined by D65).** The original definition
(content hash, extractor version) was incomplete: the *converter* is a coordinate too — a
better ASR or VLM re-reads unchanged bytes and produces a different `document.md`, and for
media corpora that is the **common** upgrade event — and so is the structurer (section roles
feed Selection; D56 already keys `extraction_input_hash` on `structurer_version`). The
precise model:

- the **source snapshot** — `version_id` (which bytes; changes when the *source* changes);
- the **representation** — `representation_id`: one conversion run's immutable output
  (`document.md` + source map + manifest + blocks; `media_design.md` §6). A version can own
  several representation generations; one is current (`current_representation_id`, swapped
  only on completion of the downstream chain);
- the **extraction basis** = **`(representation_id, blockizer_version, structurer_version,
  extractor_version)`** — everything whose change means "same testimony, re-derived". The
  basis coordinate is persisted on the occurrence records (`chunk_claims` via the chunk's
  representation, schema §7) and on currency transitions, so "which basis produced this
  claim occurrence" is a stored fact, never an inference.

Problem A changes a *toolchain* coordinate (converter → new representation; blockizer,
structurer, or extractor version); problem B changes the *source* coordinate (new version).
In both cases, claims from the *old* basis stand in an altered relation to claims from the
new basis — and the system needs (1) vocabulary for that relation, (2) a counting rule
invariant under basis changes, and (3) reconciliation machinery that runs when a basis
changes. But the *semantics* differ, and the design keeps them distinct — "the toolchain
changed" (same testimony, re-derived) and "the source changed" (new testimony) are formally
distinct events:

| | Re-derivation (converter / blockizer / extractor bump) | New version (content change) |
|---|---|---|
| the new claims are | a **re-transcription** of the *same* testimony | **new testimony** — the source's statement changed |
| the old claims become | redundant copies (mechanically non-current) | still-valid *historical* testimony, dated by their version |
| conflicts handled by | nothing — same testimony, nothing to adjudicate | ordinary E3 supersession/contradiction (D3/D4/D43) |

One mechanism, two rule-sets riding it. (Why "nothing to adjudicate" for re-extraction: the
re-transcribed claims are the *same testimony* — same document, same assertion, same time —
so the system has learned nothing new about the world; adjudication exists to weigh new
testimony, and there is none. Versioning's changed content *is* new testimony, hence the
ordinary supersession path.)

## 2. Document lineages, versions, and content objects (D55)

**Three identities, three jobs:**

1. **Content object** — immutable bytes, keyed `content_hash`. The idempotency and blob-reuse
   key (D12/D37, unchanged in spirit): identical bytes are stored, converted, and paid for
   once, even if two lineages carry them (the same PDF in two Drive folders).
2. **Document lineage** — the *logical document over time*, the stable `doc_id`. Identity is
   **connector-native**: `(source_kind, source_ref)` — the Drive file ID, the message ID, the
   watched URL, the upload event. Bytes cannot identify a lineage (they change — that is the
   premise); titles and paths cannot either (renames/moves are metadata changes over a stable
   `source_ref`). A genuinely new `source_ref` is a new lineage even if the content is
   similar. Everything durable anchors on the lineage: P3 paths (the F6 stability contract),
   K citations, crossrefs, GCS path prefixes (`<doc_id>/<content_hash>/…` — the layout that
   always implied this design).
3. **Document version** — one observed immutable snapshot of a lineage, pointing at one
   content object, carrying the per-snapshot state: artifact URIs, conversion/structure
   provenance, `source_modified_at` (which feeds derived claims' `asserted_at` — testimony is
   dated by *when the source said it*, D41), and processing status. Append-only; the lineage
   holds a `current_version_id` pointer.

An identical-byte observation creates no new version. It may advance the existing row's
`source_version_ref` as a connector-efficiency cursor, but it must not change or clear
`source_modified_at`: that value already participated in E2's deterministic header and
`extraction_input_hash` and fed immutable claim `asserted_at`. A source-time correction needs a
future explicit metadata-correction/reprocessing contract; cursor advancement alone is never
such a contract.

**Versioning mode — the semantic dial (per lineage, connector default + override):**

- **`snapshot`** *(the fail-safe default)*: every version is independent testimony *of its
  time*. Old-version claims remain current testimony forever — correct for versioned archival
  sources (quarterly reports fetched from one URL, contract amendments), where the old
  version's assertions were never withdrawn, only succeeded.
- **`living`**: the current version is the source's *standing statement* (rosters, wikis,
  config pages). Claims present only in superseded versions lose testimony currency (§3) —
  they stop counting toward current belief while remaining immutable history.

**Absence is never *silent* retraction — and in `living` mode, removal retracts.** The two
modes, side by side (stress-test amendment O-B; a `review` softener existed briefly and was
**removed** — see below):

| Mode | Claims existing only in old versions | A fact whose current support hits zero via removal |
|---|---|---|
| `snapshot` | stay current testimony **forever** (each version = independent dated testimony) | cannot happen via removal |
| `living` | lose currency when their content leaves the current version | **sole-support removal adjudicates the fact closed** — recorded (`retracted_source_removal`), loud, reversible |

**Why living retracts:** `living` *means* "the current version is the source's standing
statement" — once declared, a fact whose only support left the standing statement is, by that
declaration, no longer stated; keeping it served as current belief pending a review queue is
exactly the zombie-fact failure this system treats as cardinal. The wrong-retract failure is
visible (K recompiles, envelope), audited (an adjudication row), and self-healing (re-added or
newly-sourced content reopens through ordinary E3). The mechanical form of this rule: at
reconciliation, `versioning_mode = 'living'` **obliges** the worker to close any fact whose
sole current-testimony support left the current version — no flag path exists for this case.

**The removed alternative (documented, with its re-add condition).** A `removal_semantics =
review` softener (removal only withdraws support + flags, belief stands until triaged) was
designed and then **removed**: every source class it seemed to serve is served better by the
modes themselves — rolling logs whose entries scroll off are *misclassified snapshots* (their
old entries were never withdrawn), and facts sole-supported by content a messy living doc
deleted are precisely the beliefs that deserve to end. Its only real content was insurance
against mode misclassification, and the fix for misclassification is the right mode, not a
softer wrong one. Re-add condition: a measured source class whose false-retract rate is
unacceptable *and* which snapshot genuinely cannot serve (old-content search pollution being
the symptom to check). Note the **`support_withdrawn` review flag survives independently** —
it is the *re-extraction* zero-support path (D54 §4: a new extractor generation fails to
re-derive a fact), not a removal-semantics artifact.

**The retract action is per-shape.** (Recall: an *observation* is a fact stating a value about
one entity — `concepts.md` §0 — and comes in two shapes: an **effective state** that holds
until it changes — a headcount, a status, an employment — and a **fixed-period measurement**
whose truth is scoped to its period — "FY2023 revenue was \$5M". Which shape an observation is
is the adjudicator's semantic judgment over its statement, exactly as in ordinary supersession
— the binding rules are `observations_design.md` §3.) The retract action routes through that
same judgment: for relations and **effective-state** observations
(headcount, status, employment), cap `valid_until` at the version's `source_modified_at` —
"held until the source withdrew it" is coherent world-time. For **measurement / fixed-period**
observations ("FY2023 revenue was \$5M"), capping valid-time would violate the no-cap rule (the
figure doesn't stop being true *of FY2023*) — retract instead sets `invalidated_at`
(transaction-time: "we no longer hold this belief; the source withdrew it"). Both record
`retracted_source_removal`. In every case: other-lineage support → decrement only; and a
source can always retract *explicitly* by asserting a retraction — a claim, adjudicated like
any other.

**Connector guidance (spike 6's starting point):** native edit-in-place formats (Google
Docs/Sheets) → `living`; replace-whole-file uploads (PDFs, exports) → `snapshot` — a replaced
PDF is usually a different report, not an edited standing statement; rolling logs
(changelogs, status docs with scroll-off) → `snapshot`, their trimmed entries were never
withdrawn. The heuristic: *edit-in-place leans living; replace-whole-file and rolling logs
lean archival.* (Changed content that asserts something *different* is the easy
case: it is new testimony flowing through ordinary E2→E3, where supersession does exactly
what it was built for — a roster edited from "headcount 500" to "600" is the observations
design's worked example arriving through one lineage instead of two documents.)

**Ingest debouncing.** A file being actively edited (five saves in an hour) coalesces to one
ingested version per stability window (ingest only after N quiet minutes — the D12 debounce
discipline at the connector). A connector poll that finds an unchanged revision/etag is a
no-op before any bytes move; unchanged bytes are a `content_hash` no-op as today.

## 3. Testimony currency (D54)

One new, deliberately narrow notion: a claim is **current testimony** iff it belongs to its
lineage's current extraction basis *under the lineage's mode*:

- **re-derivation**: when extraction-basis generation v2 completes for a (version, chunk),
  that chunk's v1 claims flip non-current (`reextracted` — the reason covers any
  basis-coordinate change: an extractor bump, a **converter bump** (a new representation — a
  better ASR/VLM re-reading unchanged media bytes, the common media event, D65), a blockizer
  bump, or a structurer bump) — wholesale, by coordinates; no content matching;
- **`living` version supersession**: claims whose chunks are absent from the new current
  version flip non-current (`version_superseded`);
- **`snapshot`**: version succession flips nothing.

**What currency is not.** Not supersession, not invalidation, not a validity judgment — no
adjudicator is involved, no `invalidated_at` exists, and nothing about the claim itself
changes (text, spans, asserted validity, assertion time: immutable forever, D3 fully intact).
Currency is processing bookkeeping — the same epistemic category as "this Lance row belongs
to a superseded embedding version." Mechanically it is the D33 pattern: an **append-only
transitions ledger** is the truth (timestamped, reason-coded, replayable — D7), and a cached
flag + partial index on `claims` is the hot-path filter. Because transitions are timestamped
events, transaction-time reconstructions ("what did we believe in March?") still see the old
generation's contribution — nothing is overwritten.

## 4. The counting rule (D54)

> `evidence_count` (and `contradict_count`; relations *and* observations) ≡ **the number of
> distinct document lineages** whose **current-testimony** claims support (resp. contradict)
> the fact.

One redefinition, three inflations dead: re-extraction (new generation, same lineage → no
change), version churn (an hourly-edited doc re-asserting a fact per version → one lineage),
and within-document repetition (three paragraphs asserting X → one lineage — corroboration is
a *source* property; within-source emphasis is not corroboration, and D42's independence math
gets its natural denominator: distinct *external* lineages). The evidence *rows* stay
claim-grained and append-only — every generation's and version's link survives as provenance;
only the aggregate's definition changes. Consumers keep their names and get the semantics
they always assumed.

**Zero current support — two causes, two opposite treatments.** The rule that decides them:
*did the triggering event say anything about the world, or only about our own processing?*

- **The source or a curator acted** — a living document's edit removed the content (§2), the
  document was deleted at its source, or an operator deleted a version/lineage from the corpus
  (§8). These events carry a decision ("this is withdrawn" / "this no longer belongs in the
  memory"), so the system acts on it: solely-supported facts are **closed, per shape**
  (states: `valid_until` capped; fixed-period measurements: `invalidated_at` — the D43 no-cap
  rule), recorded as a `retracted_source_removal` adjudication. Loud, attributed, reversible —
  **no flag, no queue, no limbo.**
- **Only our transcription changed** — a new toolchain generation (an extractor bump, or for
  media the common case: a converter — ASR/VLM — upgrade) re-read the *unchanged* file and
  did not re-derive a claim. The file still says what it always said; the event carries no
  information about the world, and its two possible explanations demand **opposite** actions
  (the old claim was an extraction artifact → the fact should be marked wrong; the new
  extractor regressed → the fact is fine and the extractor needs fixing). No mechanical
  verdict is derivable, so the fact is **flagged `support_withdrawn`** into the review queue
  (D24 machinery) with the diff attached, for a reviewer — human or the designated reviewer
  agent — to decide. **This is the flag's only trigger.**

**Triage mechanics** (the reviewer — human or the designated reviewer agent — investigates
via the ledgers: the source chunk in `document.md`, the old claim's grounding span, and above
all `claim_extraction_decisions` — did the new extractor *drop* the content with a recorded
reason, or never consider it at all?). Exactly two terminal verdicts, both recorded on the
`review_queue` row: **`restore_support`** — the old claim was right, the extractor regressed:
a `testimony_currency_events` row (`became_current = true`, reason `review_restored`)
reinstates the old claim as the chunk's current transcription until a fixed extractor
re-derives it; the recount restores the fact's support, the flag closes, and the case is
planted as a D35 canary so no future extractor ships while missing it. **`invalidate_fact`** —
the old claim was an extraction artifact: the fact's `invalidated_at` is set with a recorded
adjudication; it leaves the current fact layer entirely (history keeps the full record).
`uncertain` is the only non-terminal outcome and leaves the marker standing — deliberately
visible, meant to be rare.

While flagged, the fact carries `support: withdrawn` in the retrieval envelope, so agents see
the ground moved before planning
on it. Two guards bracket the flag: D35's planted canaries catch known regressions *before* an
extractor rolls, and the **flag rate per extractor version** is the live rollout canary — a
spike right after an upgrade is the corpus-level regression alarm (rollback signal, D22
harness).

## 5. Reconciliation — one flow for both problems

Runs when a lineage's basis changes, and only on **completion** (a new version's extraction
finished; a version-bump re-extraction finished — never mid-flight, so there is no window
where old support is gone and new support hasn't landed; slots into the orchestration lanes'
completion semantics). One timing rule protects `retract` semantics from **moves**, and it is
explicit state, not convention (Codex review F8): connector polls run as recorded **sync
cycles** (`connector_sync_cycles`; each ingested version stamped with its cycle), and
retraction evaluation runs only as a **cycle-finalization job** — after every lineage the
cycle observed has completed extraction. A section *moved* to a new document within one cycle
thus resolves as a support swap (old lineage withdraws, new lineage arrives) — never
retract-then-reassert; a lineage still extracting at finalization defers its retraction checks
to the next finalization (recorded grace). A cross-cycle move leaves a short, visible,
self-healing gap (a named spike, not papered over):

1. **Diff** the lineage's evidence links: facts evidenced under the old basis vs the new.
2. **Transition currency** per the §3 rules (append ledger rows; update the cache).
3. **Recount** affected facts — bounded and indexed: the touched set is exactly the lineage's
   evidence links.
4. **Apply the zero-support policy** (§4) where counts hit zero.
5. **Emit `evidence_changed`** (the D45 trigger queue) carrying the *fact-level* delta — the
   affected fact IDs with what happened to each, e.g.
   `{relations_closed: [r1], observations_closed: [], facts_recounted: [{id: r2, evidence_count: 3→2}], flags_raised: []}`
   — fact IDs and outcomes, never raw claim IDs (the K stale-storm guard).

**Worked example, end to end.** Living document A; version 1 was extracted by extractor v1;
chunk with block `b2` yielded claim `c1` ("Alice works at Acme") → relation `r1
(Alice, works_for, Acme)`, `evidence_count = 1` (this lineage is the sole support). Version 2
arrives with `b2` deleted:

1. **Diff**: the block diff shows `b2` absent; `c1`'s content is not in version 2's chunks.
2. **Currency**: `testimony_currency_events` row for `c1` (`became_current = false`, reason
   `version_superseded`, stamped with this run's `reconciliation_id`);
   `claims.is_current_testimony` → false. `c1` itself is untouched — permanent history.
3. **Recount**: `r1.evidence_count` recomputes to 0 (distinct lineages with current-testimony
   support).
4. **Zero-support, source acted** (living mode): `r1` is closed per shape — a relation, so
   `valid_until` = version 2's `source_modified_at`; a `relation_adjudications` row records
   outcome `retracted_source_removal`. No flag.
5. **Emit**: `evidence_changed {relations_closed: [r1]}` → K pages citing `r1` recompile;
   default fact queries no longer return it; history queries still do.

**K stability rules** (the stale-storm guard): a compiled page's `inputs_hash` is keyed on
**fact state** — fact IDs + validity/currency fingerprints + counts — never on raw claim IDs,
so a re-extraction that changes no fact state stales nothing. Claim-grain citations (the
attributed-statement residue) key on `(lineage, chunk_content_hash)` — coordinates stable
across re-extraction by construction. "A new claim row exists for the same testimony" is
*not* an evidence change and emits nothing.

## 6. Content-addressed reuse (D56) — cost proportional to the edit

The efficiency ladder for the hourly watcher, cheapest exit first:

1. **Connector metadata no-op** — unchanged revision/etag: no fetch.
2. **Content-object no-op** — bytes hash to a known object: new version row, everything else
   reused (and `UNIQUE` dedup means a copy in another folder reuses conversion too).
3. **Conversion reuse** — same content object + same converter version: artifacts reused.
4. **Chunk-grain extraction reuse** — the load-bearing lever. E2's idempotency key is the
   **`extraction_input_hash`** — a fingerprint of **stable components only**: the chunk's own
   block hashes + neighbor-chunk block hashes + stable header facts (deterministic document
   metadata: title, source kind, source-modified date, language) + the extractor version + the
   structurer version (a stable config string — a deliberate structurer bump is a
   re-extraction boundary, since section roles feed Selection).
   **No LLM output participates in the key** (section paths, summaries, and the E1 prefix are
   non-deterministic across re-runs and would make the key unmatchable — the ~0%-reuse hazard;
   LLM-derived context is instead *carried forward* for unchanged regions, D7 replay
   discipline). A chunk whose key is already extracted for this lineage **reuses its claims**
   (the new version's chunk row points at them); a chunk whose *neighbors* changed correctly
   re-extracts even though its own text didn't. Embeddings reuse on (chunk content hash,
   embedding version) the same way. **The mechanics — block-hash diff alignment,
   anchor-stabilized boundaries, the carry-forward rules — are bound in
   `e1_chunks_design.md` §7 (D57–D58)**; this design owns the contract.
5. **Delta-only downstream** — untouched chunks fire no currency transitions, move no counts,
   stale no K pages.

Walkthrough: a 50-page Drive document gets a two-paragraph edit. The watcher fetches once;
conversion produces the new Markdown; chunk hashing shows ~2 changed chunks; E2 runs on ~2
chunks; reconciliation diffs and finds the handful of affected facts; everything else — ~148
chunks' claims, embeddings, evidence links, K pages — carries forward untouched. **Cost ∝
the edit, not the document.**

Honest boundary: chunk-boundary shift. An edit that changes section structure can re-align
chunk boundaries so unchanged text lands in differently-hashed chunks. Section-aware chunking
(E1 splits on PageIndex sections) bounds the blast radius to the edited section; the reuse
hit-rate on real edit patterns is spike 1, and boundary-stabilized (anchor) packing is **bound in
`e1_chunks_design.md` §4** — the hit-rate spike measures its parameters, not whether it
exists.

The **occurrence record is exact** (Codex review F4): a thin `chunk_claims` map links every
version-chunk to the claims it carries — written on fresh extraction *and* on reuse — so one
immutable claim attaches to every version that carried it, and duplicate identical chunks
within one version stay distinguishable. `claims_as_of` ("what did the roster page assert in
March?"), currency transitions, and the `(lineage, chunk)`-grain K citation keys read this
map (schema §7).

## 7. Retrieval and P1 touches

- Claim-grain primitives and recipes default to **current testimony**;
  `include_superseded_testimony` is the audit opt-in; the envelope's evidence-grain answers
  disclose which regime answered (extends D49's regime disclosure).
- `claims_as_of` is historical by definition and runs over **all** testimony — versions
  finally give it a real corpus (per-version assertion times via chunk membership, §6).
- **P1 claim search** indexes current testimony into the default channel — one current claim
  per assertion (re-extraction *replaces* the searchable claim rather than accumulating
  generations); the audit channel sees everything. A representative-text change re-embeds
  that claim; it never moves a count.
- Fact-grain answers gain a `support: current | withdrawn` marker where relevant (§4).

## 8. Deletion — deletion removes the document's contribution, uniformly

One rule for every deletion grain and every document type (user decision — no per-mode
split): **deleting a document removes its contribution to the memory.** Claims are retained
as history (normal deletion keeps the audit trail; only hard-forget scrubs content — Codex
review F12), their currency ends, counts recompute, and **facts solely supported by the
deleted material are closed** per the §4 source-acted rule — recorded, reversible, no flag.
Facts also supported by other documents simply lose one supporter. (The split-a-document
scenario works exactly as expected through this rule: the four successor documents re-assert
the same facts — same fact rows, new evidence links — so when the original's deletion lands,
the facts stand on the successors' support; claims/facts being separate layers is what makes
the information survive its source's reorganization.)

- **Delete a version**: that version's testimony ends (`version_deleted`); the lineage
  continues; bytes purge if no other version references the content object.
- **Delete a lineage** (operator): the §13 document cascade at lineage grain.
- **Source-observed deletion**: the connector finds the file deleted at its source (a trashed
  Drive file) — treated as a lineage deletion through the same cascade, stamped with the
  observing sync cycle (the cycle barrier still applies, so a delete-and-recreate or a
  split-then-delete within one sync pass resolves as support swaps, not close-then-reopen; a
  cross-cycle split leaves a brief, visible, self-healing gap).
- **Hard-forget**: D74's separate fail-closed workflow spans all versions of the selected lineage.
  It first reuses this normal currency/counting transition, then scrubs source-bearing history,
  purges active P1/P2/P3/K surfaces, and records the portable restore barrier defined in
  `hard_forget_design.md`; a soft tombstone alone is explicitly insufficient for S55.

## 9. The rejected alternative — reified evidence bases (a documented alternative)

The Codex parallel analysis proposed a first-class `evidence_bases` identity ("this source,
this source-local assertion") with the evidence joins re-keyed onto it — which requires a
**cross-generation assertion matcher** (fingerprints over spans + normalized text) deciding
when two claims "are the same assertion." Rejected here (SYNTHESIS §2): the matcher is the
riskiest component in either proposal — its author's own top failure modes are all matcher
failures, and a false *split* silently resurrects the very inflation this design kills —
while every consumer it serves is servable from coordinates the pipeline already records
(lineage-distinct counting; fact-state K hashing; `(lineage, chunk_content_hash)` citation
keys; chunk-membership occurrences). **Documented alternative, adopted only on measured
insufficiency of coordinate keys** (never assumed): the basis layer in **exact-key mode only**
— never semantic matching. What was adopted *from* that proposal: `extraction_input_hash`,
`content_objects`, the P1 representative policy, and the K fact-state staleness rule.

## 10. Decision interactions

| Decision | Effect |
|---|---|
| D2 | **refined**: the count's denominator = distinct current lineages; the M:N link layer unchanged |
| D3 | **untouched**: currency is bookkeeping, never validity; no claim is superseded/invalidated |
| D12/D25 | **extended downward**: the same content-hash idempotency at chunk grain (`extraction_input_hash`); version ingest is the same key one level up |
| D33/D7 | **followed**: currency transitions are an append-only ledger; reconciliation replays |
| D37 | **refined**: `content_hash` identifies a **version** (via content objects); lineage identity is `(source_kind, source_ref)`; the GCS layout already fits |
| D41 | **enriched**: `asserted_at` from `source_modified_at` per version — living documents get real testimony timelines |
| D42 | **composes**: independence math = distinct *external* lineages |
| D43 | **works as designed**: version-to-version value changes are ordinary supersession; observation counts follow the new rule |
| D45–D47/D73 | **reused**: reconciliation emits `evidence_changed`; K `inputs_hash` is keyed on fact state (stale-storm guard); authored K2 principles citing or watching the fact receive review notifications rather than machine edits |
| D48–D51 | **small additions**: current-testimony defaults, regime disclosure, P1 channels |
| Deletion §13 / S55 | **composes**: version grain added; normal lineage delete remains here, irreversible forget is D74 |

## 11. Spikes (measure before locking)

1. **Chunk/extraction-input reuse hit-rate** on a real watched corpus — measured under the
   A1–A3 mechanics (moved to `e1_chunks_design.md` §10 spike 4; tracked there).
2. **Conversion cost floor per source type** — Google-Docs export vs PDF vs office: how much
   work is unavoidable before chunk hashes can compare.
3. **Zero-current-support policy** — false-withdrawal rate on the golden set (needs the E2/E3
   eval harness, `questions.md` #14); flag-only vs auto-invalidate per mode.
4. **Connector identity rules per source kind** — `source_ref` stability, rename/move/copy/
   fork/deletion semantics; a per-connector table (Drive, IMAP, URL, upload).
5. **Reconciliation cost at hub lineages** — a lineage evidencing thousands of facts.
6. **`versioning_mode` defaults per connector/document class** — is Drive living or snapshot
   by default; validate the edit-in-place vs replace-whole-file vs rolling-log heuristic on a
   real corpus.
6a. **Cross-cycle move gap** (the retract × move interaction, §5) — load-bearing since living
   always retracts: a cross-cycle move produces a brief *wrongly-ended* belief (visible,
   self-healing). Measure frequency on a real corpus; decide whether a grace window is
   warranted. The **false-retract rate per source class** is also the number that would ever
   justify re-adding the removed `review` softener (§2).
7. **Version retention × hard-forget** — retention policy for old versions and their
   artifacts vs S55 obligations.
8. **P1 representative policy** — search diversity vs lost phrasings, measured.

## References

Research: `plan/analysis/evidence_lifecycle/` (internal_analysis.md, external_agents/codex.md,
SYNTHESIS.md). Decisions: **D54–D56** (this design), D2, D3, D7, D12, D24, D25, D33, D35, D37,
D41, D42, D43, D45–D51. Schema: `postgres_schema_design.md` §6 (lineages/versions/content
objects), §7 (chunk reuse keys), §8 (currency), §9/§9.A (counts). Adjacent: review finding F3
(`design_review_2026_07.md`), the orchestration lanes (PR #29) for reconciliation-on-completion.
