# Design review — full-scope conventional embedding input architecture

**Reviewer:** Claude Fable (independent design review pass)
**Date:** 2026-08-03
**Status:** analysis, non-binding. Reviews `FULL_SCOPE_ARCHITECTURE.md` (the proposal);
nothing here amends `plan/designs/` or `decisions.md`.
**Read:** the proposal, `PROBLEM.md`, `SYNTHESIS.md`, `e1_chunks_design.md` (§1, §5, §7),
D63, and the code paths cited below (verified against source, not against the proposal's
description of them).

**One-paragraph position.** The proposal's core move — split *location facts* (structured
coordinates) from *embedding-input policy* (a pure, versioned render function) from
*embedding text* (the exact string embedded) — is correct, and it fixes a latent
correctness wart the current design carries silently (§2.7 below: A3 carry-forward can
serve a *stale* location description today). Ship it as design direction. But two things
must change before it can bind: **structured location must stay inside E2's grounding
union** (the proposal's "header out of grounding" preference, taken literally, would break
located claims exactly where `body_only` mode needs them most), and **the connector
metadata contract the Slack story silently assumes does not exist anywhere in the repo**
(`SourceItem` carries no channel/user/thread/time — the proposal's central corpus story has
no data source). One structural simplification: do not turn pure functions into durable
ledger stages, and do not store the full embedding text at chunk grain in Postgres.

---

## 1. High-level decisions — agree / disagree / amend

### H1 — Conventional-only + interchangeable embedders; contextual non-goal — **Accept with changes**

The owner constraint is reasonable on its own merits: interchangeability is what makes
D63's "hardest thing to change later" risk survivable, and the contextual branch has a
real, under-acknowledged reuse problem (a vector that depends on *neighbor* content cannot
be reused keyed on the chunk's own content hash — `SYNTHESIS.md` §2 Codex finding 3;
D56 carry-forward as implemented in `spine/chunk_catalog.py` `_SELECT_CARRY_FORWARD`
would be silently unsound for it).

The change: **do not delete the contextual branch's design; restate it.** D63 currently
binds it as "the fully designed alternate configuration" (`decisions.md:1976-1980`), and
`e1_chunks_design.md` §5 designs both branches. The correct full-scope move under this
repo's own rules (CLAUDE.md Rule 2) is a *scope boundary*: "contextual embedders are a
documented non-goal for the product; the alternate branch remains designed and inert."
Deleting written, harmless design content buys nothing and costs optionality. The D-entry
amendment should demote, not erase.

### H2 — Split location facts / embedding-input policy / embedding text — **Accept**

This is the proposal's best idea and the root fix. Today one string is simultaneously:
vector input (`e1.py:222-225`), the stored P1 passage text (`e1.py:251-264` — `text` on
`P1ChunkRow` is prefix+body), an E2 grounding-union member (`e2.py:717-718`), an E2 bundle
line (`e2.py:679`), and an LLM product with its own version stamp. Five consumers, one
overloaded column, and every change to any consumer's needs perturbs the other four. The
triple (typed facts, versioned pure policy, derived text) gives each consumer its own
contract. No objection; this should bind regardless of what happens to any other decision
in this review.

### H3 — Default path: no LLM for location; deterministic render only — **Accept with changes**

Accept the direction. The evidence is on its side even before the owed measurement: the
current per-chunk LLM call is handed a numeric section path (`0.2.1`), a constant title,
and the first 400 characters of the very body it precedes (`e1.py:331`, `:345-375` — see
`SYNTHESIS.md` §2 Claude finding 1), so its marginal information over a title-aware
template is plausibly near zero. And a deterministic render is the only variant whose
failure probability does not decay geometrically in document size.

Two changes:

1. **Bind the mechanism unconditionally; bind the default *content* through the owed
   measurement.** `e1_chunks_design.md` §10.8 has owed "the context prefix's retrieval
   contribution" since D63. The binding amendment should freeze the policy machinery
   (modes, facts, versioning, re-embed triggers) and state the deterministic default as
   the design intent, with the S2-style A/B (`SYNTHESIS.md` §3, sequence item 7) as the
   acceptance gate on the eval program — this repo's own "numbers are starting points to
   measure" discipline, applied to a policy rather than a threshold.
2. **Decide the LLM escape hatch explicitly, don't hedge it.** §6.1's "optional future
   genre escape hatch only" is exactly the "later/defer" framing CLAUDE.md Rule 2 bans
   from designs. Either the LLM location path is a designed, versioned policy variant —
   which then *must* inherit the durability machinery (checkpointed per-unit commit) the
   monolith lacked, or it is a documented non-goal. Pick one in the binding text.

### H4 — Location header is conditional, not always-on — **Accept with changes**

The principle ("a collision-reduction device, not a moral requirement") is right, and the
predicates are deterministic functions of facts + body, which keeps them inside the
version stamp. Three amendments, one of which is load-bearing:

1. **Pin the token counter (load-bearing).** `T_short`, `H_max`, and α are denominated in
   tokens. Tokens *of which tokenizer*? If the policy counts with the embedding model's
   tokenizer, the mode decision — and therefore `embedding_text` itself — depends on the
   model, and "same policy version, different model" silently produces different strings:
   interchangeability broken by the very design that exists to protect it. The chunker
   already owns a deterministic token counter (`token_count` on chunk rows, packed by
   semchunk); the policy must count with a policy-owned, pinned counter that is part of
   `embedding_input_policy_version`, never the model's.
2. **Make recompute-and-compare the reuse test.** Because the render is pure and cheap,
   the safe reuse rule is: recompute `embedding_text`, hash it, reuse the vector iff
   `(policy_version, model, text_hash)` matches (the proposal's own work-unit rule 3).
   This automatically makes every mode flip safe — a document growing from one chunk to
   two flips `body_only → location_header`, the hash changes, the re-embed happens, no
   special case needed. State this as *the* mechanism so nobody builds a facts-equality
   comparison instead.
3. **Predicate 1 and the N_chunks dependency are fine but should be named:** the mode is
   a function of document-level state, so a chunk's embedding text can change when *other*
   chunks appear. Rule 2's hash discipline absorbs it; the design text should say so.

### H5 — Short Slack-as-one-doc → body_only + P1 scalars; long export → compact deterministic header — **Accept with changes; Needs decision on the metadata contract**

The retrieval logic is sound: a five-word body under a 40-token header is a vector *about
the header*; location for message atoms belongs in filterable scalars and the claims
channel (D58's needle/passage split), not in prose glued to the body. The mode table in
§3.2 is the right product behavior.

But the proposal builds on a contract that does not exist. **Verified:** the watched-source
port (`ports/connector.py`, `WatchedSourcePort`) yields `SourceItem` rows carrying exactly
`source_ref, revision, modified_at, deleted, filename, mime`
(`model/documents.py:145-155`) — no channel, no author, no thread, no message timestamp.
`documents` carries `source_kind, source_ref, source_uri, title` (`document_catalog.py:528`);
`ChunkSource` (`model/chunks.py:33-51`) threads through title/kind/dates/language and
nothing else. Every Slack-shaped location fact in §2's table — channel, user, thread_id,
time range, speaker — has **no path from any connector into the chunk layer**. The
proposal says facts come "from E0/E1 structure + connectors" and never notices that the
connector half of that sentence is an unbuilt, undesigned D61 port amendment plus storage
plus threading. This is the single largest gap in the document (see §3.1 below), and it is
a **decision**, not a detail: the shape of typed source metadata per `source_kind` is a
binding connector contract.

Also under-specified: *who chooses the ingest shape* (one message = one document vs
channel export = one document)? The policy table conditions on the shape but nothing
defines where that choice lives (connector contract? deployment config?). At Slack scale,
one-message-one-document means millions of tiny documents each paying full pipeline
overhead (conversion, PageIndex structuring, section rows) for a one-line body — the
policy is designed, the economics of the shape it presumes are not.

### H6 — PageIndex summaries stay out of default embedding text and out of E2 grounding — **Accept**

Consistent with D79 and already half-true in code: summaries are deliberately absent from
the grounding union today (`e2.py:696-698` — "Section summaries are deliberately absent;
D79 orientation text must never become a fact-injection path"). Keeping them out of
embedding text closes the other half: since P1's stored `text` *is* the embedded string,
summary text in the embed input would also become agent-readable passage text and
FTS-indexed content — second-order claims leaking into the passage channel through a side
door. No change requested.

### H7 — Replace document-level all-or-nothing embed with a multi-unit durable graph — **Accept with changes**

The durability goal is mandatory — the current shape (all prefixes, then one embed call,
then one write-back; `e1.py:212-276`) is the proven failure and both prior analyses killed
it. The amendment is about *where the durable units are*:

**Draw stage boundaries at failure boundaries, not concept boundaries.** In the proposed
default path, `resolve_location_facts` and `render_embedding_text` are pure functions over
data already in Postgres. They cannot fail transiently — no provider, no network, no
nondeterminism. Making each a per-chunk durable ledger unit (§4 rule 2) is machinery
without a failure mode to manage: recomputing them is free, forever. The only stage that
can actually fail is `embed_chunk_batch`. So the graph that matches the risk is:

- facts + rendered text (or hash — see §2.3) written as columns in one pass, idempotently
  recomputable, **no** per-chunk ledger state;
- embedding in bounded batches, **per-batch durable stamps**, unique
  `call_key=f"embed_chunks:{batch}"` — required, because the cost ledger's idempotency is
  `ON CONFLICT (deployment_id, processing_id, attempt, call_key) DO NOTHING`
  (`spine/work_ledger.py:808`): sub-batches sharing today's fixed `"embed_chunks"` key
  (`e1.py:238-240`) would silently discard every batch's cost after the first;
- document readiness = all batches stamped.

The proposal's own rule 2 already permits this ("or an equivalent spine 'stage stamp' that
is queryable and restart-safe") — the binding design should *choose* it rather than offer
the choice. Full per-chunk `processing_state` fan-out re-opens the fan-in problem (who
enqueues extraction when the last of 749 rows completes?) and the ~10⁸-substrate-rows
regime `e1_chunks_design.md` §2 explicitly keeps out of Postgres, for no durability the
batch stamp doesn't already buy. Both prior analyses converged here
(`SYNTHESIS.md` §1, long-term ranking item 4); the proposal should stop hedging.

**A simplification the graph enables and the proposal misses:** once location is
deterministic, the E2 bundle no longer waits on any LLM- or provider-produced artifact —
today extraction chains from embed completion (`e1.py:277`, `_extract_follow_up`) although
it never uses vectors, and tolerates a missing prefix (`e2.py:679`). Chaining
`extract_claims` from *rendered facts* instead of *embedded batches* removes an entire
provider-flake class from E2's critical path and roughly halves first-ingest wall-clock.
The old blocker (a chunk extracted before vs after its prefix lands reads different
bundles under the same `extraction_input_hash`) dissolves — deterministic facts are the
same bytes whenever read. Take the win; it falls out of the architecture being reviewed.

### H8 — Promote via new e1 design section + D63/e1 §5 amendment + orchestration update — **Accept with changes**

Right promotion path, wrong home for the new section, and an under-counted blast radius:

- **Home:** a section *inside* `e1_chunks_design.md`, not a standalone design.
  The policy is the input contract of the P1 chunk vector; e1 already owns embedding
  granularity, the D63 branch, and A3 carry-forward. A standalone doc describing the same
  artifact e1 §5 describes is a drift generator. (If it must be standalone for size, e1 §5
  should shrink to a pointer — one owner, never two.)
- **Blast radius:** the proposal's §6 lists e1, D63, orchestration, storage, E2. It omits
  **`retrieval_design.md`** (the new P1 scalars are recipe surface — the `search` channel
  spec enumerates filters, and scalar prefilters run before ANN) and the **workers
  inventory** row that D63's consequences bound to "exists" (`decisions.md:1997-1998` — the
  amendment must rewrite that consequence, not contradict it). List every touched artifact
  in the amendment PR or the boundary erodes silently.

### H9 — Free-form rendered header out of E2 grounding union; structured location for E2 instead — **Accept with changes (the changes are load-bearing)**

Split this into the two decisions it actually contains:

1. **LLM free-form prose leaves the union: yes, unambiguously.** The stored prefix is
   today the union's only LLM-derived member (`e2.py:717-718`); with it gone the layer-2
   grounding union becomes fully deterministic and source-derived — a strictly better
   posture, and one both prior analyses flagged as a free bonus.
2. **Location leaves the union: no — this would be the proposal's one fatal-flaw-shaped
   error if implemented as written.** §6.4's preference ("only body + source-derived
   spans, header out of grounding") removes location *tokens* from what E2 may quote in
   `added_context`. But decontextualization is precisely the act of writing location into
   claim text — "In #eng-alerts, Alice said the migration was fine" needs `#eng-alerts`
   and `Alice` to pass the layer-2 membership gate (`_failed_added_context_tokens`,
   `e2.py:722-741`). And `body_only` mode makes this *worse*, not better: it deliberately
   moves the location burden off the passage vector and onto claims + scalars, so the one
   channel that still carries location in prose — claim text — must be able to ground it.
   Naive removal means located claims get rejected exactly where the new policy depends on
   them most, and both channels go location-blind.

   The fix is already modeled in the code: render **structured location facts as a
   deterministic union element**, exactly as `document_header` is one today
   (`e2.py:701-703`). `("structured_location", render(location_facts))` replaces
   `("context_prefix", …)`; the union stays deterministic *and* location-complete.

   Sequencing note: any change to union membership changes which `added_context` is
   accepted → `E2_EXTRACTOR_VERSION` bumps → corpus-wide re-extraction. That is one
   deliberate boundary, scheduled, not a side effect discovered in production.

---

## 2. Mechanism deep-dives

### 2.1 New design section "Embedding input policy"

**Strengths.** Right scope (modes, predicates, knobs, versioning, re-embed triggers, the
Slack table, the claims-vs-chunks relationship). Knob values labeled as starting
hypotheses — matches house rules. Policy version in the embed generation stamp with the
model id (§5) is exactly right.

**Risks / gaps.**
- The **normalization function** (`normalize(body)` in §9) is named and never specified.
  It participates in `embedding_text_hash`, so it needs the blockizer treatment: pinned
  algorithm (NFC? whitespace? line joining?), versioned inside
  `embedding_input_policy_version`, regression-tested. An unpinned normalizer is silent
  hash drift.
- The **token counter** must be policy-owned (see H4.1).
- **Re-embed trigger enumeration** should be exhaustive in the binding text: policy
  version change, model change, text-hash change — and *nothing else* (in particular, a
  structurer re-run that redraws sections *does* change section-title facts and therefore
  hashes; say whether a structure generation bump is intended to trigger re-embedding, or
  whether facts pin to the structure generation the chunks were cut under — I believe the
  latter, via `current_structure_generation_id`, but the design must say it).

**Recommendation.** Keep the section; house it in e1 (H8); add the normalizer and counter
pins and the trigger enumeration.

### 2.2 P1 scalars expansion

**Strengths.** `section_role` already exists as the pattern (`P1ChunkRow`,
`model/chunks.py:157`; role-filtered defaults in `e1_chunks_design.md` §5). Lance scalar
prefilters run before ANN (`retrieval_design.md` — "scalar prefilters run before ANN"), so
channel/user/time filters are cheap and the cardinalities (channels 10³–10⁵, users
10⁴–10⁶, timestamps) are unremarkable for Lance scalar indexes.

**Risks.**
- **Recipe surface is design surface.** Each new scalar must appear in the `search`
  channel's filter enumeration in `retrieval_design.md`, or it exists in storage and not
  in the product. The proposal treats scalars as a storage question; they are mostly a
  retrieval-design question.
- **Privacy/deletion.** User identifiers as Lance scalars on ~10⁸ rows create a new
  deletion obligation: "forget person X" must reach P1 by filter, not only by doc. The
  forget/purge ports exist (`ports/forget.py`, `ports/purge.py`); the design must state
  that message-metadata scalars are inside their contract.
- **What goes where.** Keep the Lance set small and recipe-driven: `source_kind`,
  `channel`, `author`, `ts` (+ existing `section_role`) — the things queries actually
  filter on. Thread ids, reply-to chains, reaction counts, and the rest of the facts
  payload stay Postgres-side in `location_facts` (queryable via `lookup`/`scan`, joinable,
  not vector-search surface). Every scalar added to Lance is schema you migrate at 10⁸
  rows; add on demonstrated recipe need, like the D5 predicate funnel.
- **Claims are the forgotten channel.** `P1ClaimRow` has `doc_id, chunk_id` and no source
  scalars (`model/chunks.py:166-182`). "What did Alice say in #eng?" is a *needle* query —
  it rides claims — and Lance cannot join claims→documents at query time. Either claim
  rows inherit the same message scalars, or the design documents doc-list prefiltering and
  its scale limits. Unaddressed in the proposal.

**Recommendation.** Bind the funnel rule (small filterable set + JSON facts for the rest),
put the enumeration in `retrieval_design.md`, and answer the claims-channel question
before this becomes binding.

### 2.3 Storage shape

**Strengths.** `embedding_text_hash` + `embedding_input_policy_version` (+ model) as the
reuse/audit key is exactly right and replaces a fragile convention with a checkable one.

**Risks.** Storing the full `embedding_text` per chunk in Postgres is the one place the
proposal contradicts the repo's own storage discipline. The body is already in
`document.md` (object store); the render is a pure function; e1 §2 keeps blocks out of
Postgres precisely because "~10⁸ substrate rows nobody queries individually" belong in
object storage with keys in PG (D37 split). Full embedded strings at chunk grain are that
mistake with extra bytes — order hundreds of GB of redundant text at target scale.

**Recommendation.**
- Store: `location_facts` (small JSON), `header_mode`, the **rendered header** (small,
  bounded by `H_max` — this is the `context_prefix` column's honest successor and E2's
  bundle/union input), `embedding_input_policy_version`, `embedding_text_hash`.
- Do **not** store the concatenated header+body string; it is reconstructible byte-exactly
  by construction, and determinism is the audit story (replay = recompute + hash compare).
- **Never drop the legacy `context_prefix` bytes for already-extracted corpora.** Existing
  claims were grounded against a union containing those exact bytes; D33 transcript replay
  needs them. The column (or an archived equivalent) is frozen provenance, not dead
  weight.

### 2.4 Work graph

Covered under H7. Summary of position: contract-first yes; per-chunk ledger rows for pure
functions no; durable unit = embed batch with unique `call_key`s; readiness = all batches
stamped; chain E2 from rendered facts, not embed completion. One addition: define the
**typed skip** for empty bodies (§3.2's "fail or skip unit with typed reason") as a ledger
outcome, not a comment — "no silent empty vectors" needs a row someone can query.

### 2.5 Slack / message-atom policy

Covered under H5. Answers to the prompt's specific question — *when does a header still
win on message atoms?* Three honest cases: (a) the deployment's agents issue free-text
searches without filters (filters only help recipes that use them); (b) many channels
share one index and the query names the channel in prose; (c) the corpus mixes
source kinds so heavily that `source_kind` scalars alone under-discriminate. All three are
measurable, which is what §3.2's predicate 4 hedge already says — keep the hedge, run the
short-message arm in the eval program (the current eval corpus has no short-message set;
that's a gap, §3.8 below).

Also worth stating in the binding text: for channel-export documents the *body already
contains* speaker names and timestamps (the rendered transcript), so the compact header's
marginal contribution is channel/thread coordinates — which is why `H_max`-bounded compact
headers are defensible even for shortish bodies there.

### 2.6 Claims vs chunks (D58 interaction)

The conditional header is *consistent* with D58's multi-granularity story — needles ride
claims, passages ride chunks, and `body_only` leans on that split harder. Two real
interactions, one fixed by H9-as-amended, one open:

1. **Grounding:** `body_only` shifts location expression into claim text; claim text must
   ground location tokens; therefore structured location must be a union member (H9). With
   that amendment the interaction is healthy — claims get *better* location than today
   (typed facts instead of a numeric path: the E2 bundle currently renders
   `SECTION: path {chunk.section_path}` — the integer path — at `e2.py:676`).
2. **Filtered needle queries** need claim-side scalars or a documented join strategy
   (§2.2). Open.

### 2.7 A3 / D56 — carry-forward of embedding_text under policy version

**A point in the proposal's favor that it never makes:** the current A3 carry-forward is
*silently wrong about location*. `_SELECT_CARRY_FORWARD` reuses a stored prefix keyed on
`chunk_content_hash` alone (`chunk_catalog.py:214-230`) — so a chunk whose text is
unchanged but which *moved* (section grew, content reordered) keeps a prefix describing
its old position, and that stale sentence is embedded, stored as P1 text, and quotable in
grounding. Deterministic recompute-and-compare fixes this class of staleness by
construction. Say so in the amendment; it converts A3 for embeddings from "carry bytes
forward" to "recompute free, reuse the *vector* iff the bytes match," which is strictly
more honest.

**The hazard in the other direction — the re-embed cascade.** Header fields that encode
*global position* make `embedding_text` position-dependent: with `part i of n` or a
document ordinal in the header, inserting one early chunk shifts every later ordinal →
every later `embedding_text` changes → the whole document re-embeds on every prepend-shaped
edit. That is the embed-side twin of the "~0 %-reuse hazard" e1 §7 names for extraction
keys. Embeddings are cheap relative to extraction, but at watched-corpus scale this is a
real economy term, and anchor-stabilized packing (A2) protects block boundaries, not
ordinals. **Recommendation:** prefer edit-stable coordinates in headers — section title
path, source-native ids (thread ts, turn index from the source), timestamps; if ordinals
are wanted, scope them within-section and drop the `of n` denominator (n changes whenever
the section grows). Add "header-field stability under edit patterns" to the eval program
next to the reuse-hit-rate spike (e1 §10.4).

**Duplicate content at different locations** (the prompt's question): under
`location_header` mode, duplicates separate — correct, and exactly what §3.2's predicate 4
wants. Under `body_only`, duplicate bodies collapse to identical vectors; acceptable for
message atoms because scalars and doc identity still distinguish them at hydration, and
the claims channel never collapsed them. No change needed; state it.

### 2.8 Interchangeability

Two leaks to close, one confirmation:

1. **The tokenizer leak** (H4.1) — policy knobs measured in model tokens make the policy
   model-dependent. Pin a policy-owned counter.
2. **The instruction-wrapper leak.** Several conventional embedders (the qwen3 family
   included) want instruction-formatted *query* text. Fine — but any such model-specific
   wrapping must live inside the embedder adapter and never enter stored
   `embedding_text`. The stored string is the model-independent contract; the adapter may
   dress it per model at call time (documents and queries alike). If wrapper text were
   stored, `embedding_text_hash` would encode a model choice and cross-model reuse
   accounting would silently lie. One sentence in the binding design closes this forever.
3. Confirmed clean otherwise: the embed stage sees `list[str]` (`EmbeddingRequest` in
   `e1.py:231-237`), versions ride stamps, and nothing else in the proposal is
   model-shaped.

---

## 3. What is missing or under-specified

Ordered by how hard each blocks a binding design.

1. **The connector metadata contract (blocking).** No port, schema, or storage exists for
   channel/user/thread/time (verified: `ports/connector.py`, `model/documents.py:145-155`,
   `document_catalog.py:528`). Needs: a typed metadata payload on the watched-source
   contract (D61 amendment), per-`source_kind` fact schemas, storage (documents/versions
   side), threading into `ChunkSource`, and deletion semantics. Until this exists, the
   Slack column of the proposal is aspiration.
2. **Grounding-union membership of structured location (blocking).** H9 as written breaks
   located claims; the amended form (deterministic `structured_location` element) must be
   in the binding text, with the extractor-version bump sequenced.
3. **Migration and compatibility.** The proposal's program step 7 names a re-embed tool
   but designs nothing: coexistence of old (`prefix+body`, prefix in union) and new
   (policy-rendered) generations in one deployment; P1 rows whose stored `text` is the old
   concatenation; recipes across mixed generations; retention of legacy `context_prefix`
   bytes for D33 replay (§2.3); the order of extractor-version vs policy rollout.
4. **Section-title read path.** Location facts require section *titles*;
   `document_sections.title` exists (migration `p0_02_0003`, line 490) but `SectionSpan`
   has no title field and `_SELECT_SECTIONS` doesn't select it
   (`chunk_catalog.py:162-173`). The proposal depends on this fix everywhere and names it
   nowhere.
5. **Claims-channel scalars** for filtered needle queries (§2.2, §2.6).
6. **Ingest-shape ownership** for message sources — which contract decides
   one-message-per-doc vs export-per-doc, and the per-document pipeline economics of the
   former at Slack scale (§H5).
7. **Header-field stability rules** and the re-embed-cascade budget (§2.7).
8. **Eval plan specifics.** §8 step 8 names arms but no metrics, margins, or corpora. It
   needs: recall@k on the D22 golden set per arm; a *short-message corpus* (none exists in
   the eval assets today); a `T_short`/α knob sweep; and **E2 `grounding_rejected` deltas
   per arm** — changing the embed input changes P1 text and the union, so extraction
   health is part of the score, not a side effect.
9. **Normalization spec + policy-owned token counter** pinned inside the policy version
   (§2.1, §2.8).
10. **Typed empty-body skip** as a ledger outcome (§2.4).
11. **Failure-mode table for embed batches** — batch size vs the 120 s client timeout,
    provider input caps, and the `call_key` uniqueness rule (`work_ledger.py:808`) stated
    as a MUST in the orchestration text.
12. **Multi-deployment policy config.** Knobs are presumably per-deployment port config
    (D61); if so, the policy version in every stamp must be the *deployment's* resolved
    policy version. One paragraph.

---

## 4. Ranked recommendations to the author

### Must change before this can become binding design

1. **H9 amendment:** structured location stays in the E2 grounding union as a
   deterministic element; only free-form LLM prose leaves. Sequence the extractor bump.
2. **Design the connector metadata contract** (typed per-source-kind facts, port
   amendment, storage, deletion path) or scope the binding design to facts derivable from
   structure alone and mark message metadata as depending on that contract explicitly.
3. **Storage discipline:** store facts + bounded header + policy version + text hash; do
   not store the full embedded string at chunk grain; never drop legacy prefix bytes for
   extracted corpora.
4. **Pin the policy-owned tokenizer and the normalization spec** inside
   `embedding_input_policy_version`.
5. **Header-field stability rule:** no global ordinals / `of n` denominators in headers
   without a measured re-embed budget; prefer edit-stable coordinates.

### Should change

6. **Collapse the work graph to its failure boundaries:** recomputable pure render (no
   per-chunk ledger stages), durable per-batch embed stamps with unique `call_key`s,
   batch-stamp readiness. Remove the per-chunk-`processing_state` option rather than
   offering it.
7. **Chain E2 from rendered facts instead of embed completion** — enabled by determinism,
   removes a provider-flake class and roughly halves first-ingest critical path.
8. **Bind mechanism unconditionally; run the owed §10.8 measurement as the acceptance
   gate on default policy content;** resolve the LLM escape hatch to designed-variant or
   non-goal (no "future" hedges).
9. **House the policy section in `e1_chunks_design.md`** and enumerate the full amendment
   blast radius (e1 §5/§7, D63 entry + workers-inventory consequence, orchestration,
   `retrieval_design.md` filters, e2 bundle/union, decisions.md).
10. **Answer the claims-channel scalar question** (inherit scalars vs documented join
    strategy).
11. **Add the migration/compatibility section** (mixed generations, replay retention,
    rollout order).

### Fine as-is / spike later

12. Knob starting values (`T_short` 48, `H_max` 48, α 1.0) — measured constants, correctly
    labeled.
13. The Slack mode table and predicate 4's duplicate-content hedge.
14. H6 (summaries out of embed text and grounding) — bind as written.
15. Batch size B (64–128) and embed concurrency — spike S3-style.
16. `body_only` duplicate-vector collapse — acceptable; document it.
17. Contextual-branch wording — demote to documented non-goal, keep the design text.

---

## 5. Executive verdict

**Ship as design direction, with amendments — not a rewrite.** The three-way split
(location facts / versioned pure policy / embedding text) is the correct architecture: it
un-overloads a column with five consumers, deletes the geometric-failure LLM stage from
the default path, and quietly fixes A3's stale-location carry-forward. The conditional
header and the Slack scalar story are the right product behavior.

Two things stand between this and binding. First, H9 as written would remove location
from E2's grounding union just as `body_only` makes claims the only prose carrier of
location — implement it as *structured location stays in the union; LLM prose leaves*.
Second, the Slack story's data source doesn't exist: no connector metadata contract is
designed anywhere, and that contract is a prerequisite decision, not an implementation
detail. Beyond those: don't ledger pure functions, don't store the full embedded string
in Postgres, pin the tokenizer and normalizer into the policy version, and keep
position-fragile fields out of headers so the re-embed economy survives edits.

No fatal flaws with the amendments applied. The proposal should absorb them and proceed
to the e1/D63 amendment it correctly calls for.
