# Mutable facts with one world-time window

**Status:** D118, binding when merged; replaces the temporal fact design in
D107/D110–D113 as specified in §10. Design acceptance is not runtime acceptance.
**Date:** 2026-09-07.
**Analysis:** [alternatives and independent audits](../analysis/lean_mutable_fact_windows.md).
**Delivery:** [temporal work plan](../plans/temporal_clocks.md).

## 1. Concepts and the problem

A **claim** preserves what a source said. A **fact** is the engine's current
interpretation of one or more claims. Claims remain immutable; facts can change
when better evidence arrives. Relations connect entities; observations describe
one entity. Both follow this contract.

Three dates answer different questions:

| Information | Question answered | Storage |
| --- | --- | --- |
| Source timestamp | When did the source say this? | `claims.asserted_at` |
| World-time window | When did this happen or hold? | Claim D41 dates; one chosen `valid_from` / `valid_until` on each fact |
| System timestamps | When did the engine record or stop believing this fact? | `ingested_at` / `invalidated_at`, with adjudication history for revisions |

An article published in 2024 can report a tournament won on 10 May 2022 and enter
the engine in 2026. The win belongs in a query about May 2022. It remains a
believed historical fact in 2026. Its event date does not become 2024 or 2026,
and it does not need an additional world window extending forever.

The old temporal design introduced fixed fact categories and a second evidence
window to preserve historical events. Existing query time modes and the claim /
fact separation already supply the required distinction. The chosen design
removes that duplicated authority and lets ordinary adjudication update dates.

## 2. One chosen window, with honest precision

Each fact has one authoritative world-time window: the existing `valid_from` and
`valid_until`. It also stores the precision needed to interpret that window
(`valid_precision`, a new fact column using the existing claim precision vocabulary,
non-null with `unknown` as its default). SQL NULL is not a second representation
of unknown precision. Claims keep
their original dates, precision, temporal kind and source timestamp. No
`temporal_kind`, `occurs_*`, permanent seed authority, endpoint owner, or
date-dispute state is stored on a fact.

Windows use the existing UTC half-open convention: start included, end excluded.
When importing a complete claim window, reuse `core/temporal.py` canonical
arithmetic once, including a nonempty interval for an instant. A day-precision
10 May win means that calendar day, not an exact midnight event. The resulting
fact endpoints are already canonical.

Fact windows allow incomplete information that the claim schema does not:

| Meaning | `valid_from` | `valid_until` | `valid_precision` |
| --- | --- | --- | --- |
| Entirely unknown | NULL | NULL | `unknown` |
| Explicitly ongoing | Known start | NULL | `open` |
| Known start, unknown end | Known start | NULL | Unit of the known boundary; not `open` |
| Unknown start, known end | NULL | Known end | Unit of the known boundary |
| Known finite interval | Known start | Known end, strictly later | Recorded interval precision; neither `unknown` nor `open` |

The boundary units are `instant`, `day`, `month`, `quarter` and `year`. These are
shapes of one window, not categories of facts. Do not copy claim CHECK constraints
onto facts: those constraints reject the two partial shapes. Do not run claim
`canonical_bounds()` on fact rows or incomplete windows. For a bounded claim
precision that helper substitutes the start when the end is missing; a partial
fact must preserve the unknown end. Do not canonicalize known fact endpoints a
second time. The concrete fact constraints must implement this table.
When constructing a partial window from raw dates, normalize only its known
endpoint using the existing UTC truncate/advance rules (day start; exclusive
next-year end for a year), leave the missing side NULL, and store that result
without passing the partial window to `canonical_bounds()`.

Unknown and open mean different things:

- An entirely unestablished window has null endpoints and `unknown` precision.
  It must not acquire an ingestion or source date as a substitute.
- `open` precision with a null end explicitly means an ongoing, open-ended
  period. An absent end in an otherwise incomplete window means unknown, not
  proof that the fact holds forever.
- A missing start remains unknown even when an end is known. Knowing that a
  tenure ended in 2022 does not establish that it began before every earlier date.
- Known finite endpoints must form a nonempty interval. Adjudication can remove
  or replace a mistaken endpoint; SQL shape validation must permit that.

The retrieval contract in §5 distinguishes confirmed interval matches from
possible matches when a needed endpoint is unknown. Precision describes dates,
not a permanent classification of the statement.

The current fact row is authoritative. The ordinary adjudication transcript
records prior values and the reason for changing them. System timestamps on a
row alone do not reconstruct every earlier date revision; historical-belief
queries must use retained revision history where that is part of their contract.
This amendment does not claim a new historical-belief query API.

## 3. Identity and dates are one ordinary adjudication

Candidate selection uses the existing subject/predicate or entity blocks and
semantic relevance. Include believed historical facts; a finished world interval
does not make a fact an irrelevant identity candidate. Dates and source context
are inputs to the decision. They are not hard matching gates.

In particular:

- Identical text or triples do not prove two independent assertions describe the
  same event. Two tournaments can have the same participants and date.
- Different dates do not prove different events. An official report can correct
  the date of the same named tournament.
- Dated and undated claims can describe the same event or tenure.
- Claim temporal kinds can help explain testimony to the model. No copied fact
  category can veto a supported identity or update.
- High-confidence semantic adjudication can attach evidence, create a distinct
  fact, revise a fact, cap a predecessor, or preserve incompatible testimony.
  Existing confidence thresholds and conservative coexistence rules still apply.

Relation normalization must stage the assertion before identity selection. It
must not merge every live `(subject, predicate, object)` triple and ask about
identity afterward. Remove the universal same-triple overlap exclusion; separate
adjudicated identities can overlap. Retain useful nonunique candidate indexes,
foreign keys, interval shape checks, and application idempotency constraints.

### 3.1 Window updates

The existing adjudication result may carry an optional complete window replacement
and cited supporting claim IDs. Reuse its confidence and rationale. No separate
date-correction queue, operation taxonomy or review workflow is required.

| Decision field | Effect |
| --- | --- |
| Window omitted or null | Preserve the target's chosen window |
| Window object supplied | Replace both endpoints and precision, including explicit nulls |

Changing a window requires a nonempty rationale and at least one admissible
claim supplied to the adjudicator. Validate target membership, evidence scope,
UTC values and interval shape. The model may infer dates from wording and
several claims; an endpoint need not equal a single extracted timestamp.

The transcript records before/after values and supporting claims in the same
transaction as the changed fact and evidence. An evidence attachment does not
implicitly take the minimum or maximum of claim dates. The same ordinary
decision that attaches it may explicitly revise the fact's window.

For a new fact, copying the canonical claim window is valid when that window
applies to the particular normalized assertion. If one claim produces several
assertions, normalization must establish which dates apply to each. Missing
information remains missing. The first claim to create the row has no permanent
authority over subsequent evidence.

Example: one report places the Riverside final on 10 May; an official correction
places that same final on 12 May. The adjudicator attaches the correction to the
same fact and replaces the chosen day with 12 May. Both claims remain inspectable.
An additional report about a different final on 12 May creates a second fact.
There is no automatic "date disputed" state. If the evidence cannot justify
revising a date, record the ordinary decision and contradictory evidence without
pretending that the dates agree.

### 3.2 Succession, splits and evidence assignments

"Bob became CEO in 2022" can justify ending Alice's tenure at Bob's accepted
start. That is an explicit semantic update to Alice's fact. It uses a world-time
boundary justified by evidence; `now()` and source publication time are not
fallback boundaries. Another tournament win does not automatically close an
earlier win. The model explains whether the new assertion establishes succession.

A proposed end at or before a known start cannot create an empty fact window.
The adjudicator must revise the mistaken window, choose another identity, or
leave the conflicting claims visible without applying an invalid cap.

Late evidence can establish A → B → A history. The adjudicator must see the actual
original assertions and their context and explicitly decide whether there were
two A tenures. Splitting identities and moving support are one atomic decision.
Publication order or a claim falling outside a newly chosen window must not
automatically move that evidence. Retrospective and contradictory claims can
legitimately remain attached to an earlier fact.

## 4. Writes, concurrency and retry

Use the existing ledger, staging topology and domain adjudication history.
Required properties apply equally to relations and observations:

1. A normalized assertion has a stable application identity qualified by its
   source and semantic generation. A completed retry returns the recorded result;
   it does not decide identity again or repeat evidence moves.
2. Within each admitted block, drain assertions in a deterministic source order
   with stable tie breakers. A newly admitted late source is new work; this is not
   a promise that an ever-growing corpus has a globally final order. Source order
   coordinates processing and supplies no world-time boundary authority.
3. Prepare a bounded candidate/evidence snapshot, release database locks for
   remote inference, and apply only after revalidating the complete consumed
   snapshot. Candidate additions, source withdrawal/forget, identity redirects,
   fact/evidence changes and generation changes invalidate stale output.
4. Reuse one stored completed answer per exact prepared attempt. An obsolete
   attempt cannot publish an answer for a replacement attempt. Provider failure
   before durable completion follows existing retry and cost rules.
5. Apply under the deployment forget/acceptance fence, identity coordination,
   canonical block locks, and affected fact locks in stable order. All writers of
   consumed inputs participate in the same coordination. No model call holds
   those locks. An implementation must document the exact acquisition order and
   demonstrate concurrent helper and forget behavior before acceptance.
6. Facts, evidence assignments, before/after transcript and application completion
   commit together. A failed transaction leaves none of those effects visible.
   Existing derived-index/profile repair is recoverable from committed work.

These are application requirements, not a mandate for the removed temporal
operation graph, endpoint owners, cache certificates or compensation checkpoints.
Prefer existing domain transcript storage and narrowly scoped preparation/receipt
data. The implementation PR must supply its concrete DDL, lock protocol and crash
proofs; this design does not make the old D110/D113 SQL an implementation shortcut.
The delivery gate in the work plan prevents code based on unspecified storage.

## 5. Retrieval and understandable answers

Keep the existing `Validity` world endpoints and system timestamps; add precision
where needed. Do not expose verdict/occurrence windows or fact categories.
Retain the existing explicit fact-time modes:

| Mode | Question | Example |
| --- | --- | --- |
| `current` / `at` | What holds at this world instant? | Who is CEO today / in 2020? |
| `overlap` | What happened or held during this interval? | Which tournaments did Nate win in 2022? |
| `history` | What currently believed history is known up to the evaluation time? | What has Nate achieved? |

History includes completed intervals and excludes known future starts under the
existing mode's contract. Future planning uses an explicit future instant or
overlap interval. General biography/achievement callers must request history;
leaving every caller on the current default would hide completed wins.

Strict temporal predicates confirm only what the accepted dates establish. A
relevant undated fact remains a possible candidate, not a confirmed match for
every year. Assured retrieval performs the corresponding unknown-date candidate
pass and distinguishes confirmed from possible matches in its response contract.
This distinction is query-specific, not a stored dispute status. Responses must
preserve precision and unknown endpoints so consumers can explain it plainly.
Versioned assured fact results carry a per-result `temporal_match` value of
`confirmed` or `possible`, relative to the response's requested time scope. This
field describes the query match, not confidence in the fact or a stored dispute
state. A history match does not mean the fact holds now. The concrete consumer
contract must include this field in its closed response schema and surface
generation; an ambiguous mixed list or silently dropped field cannot satisfy
this design.

For example: "Three wins are dated to 2022; one additional win has no accepted
date." An exact count requires an exhaustive query over distinct adjudicated
identities with adequate date information. Top-k context and truncated candidates
cannot prove completeness. Preserve existing truncation and coverage disclosure;
do not turn missing evidence into a confident zero or an exact count.

Claim retrieval retains each source's complete date tuple and context. Text-only
deduplication must not discard differently dated testimony. Grouped evidence must
retain member identities and dates. Open SQL remains read-only published views
and functions under the sandbox query role; no new mutation capability is granted.

## 6. Profiles and generated knowledge

An entity **profile** is a compact summary of salient facts used to help resolve
which entity a new mention refers to. The current builder deterministically joins
fact text; it is not a separate authority over facts.

Profiles select date-qualified salient facts without a moving current-only
filter. "CEO of Acme, 2019–2022" remains useful history after 2022. An accepted
future appointment is labelled with its date, not phrased as already current.
Known ongoing periods are rendered "since 2019; no end recorded", not certified
as true today merely because the text is cached. Use that wording only for
`open`; a partial window with an unknown end says "start known; end unknown",
without implying it is ongoing. Undated assertions are labelled as undated.
Selection/ranking cannot depend on changing wall-clock recency unless
that dependency participates in refresh; use stable fact/source ordering here.

Fact, evidence, date, identity, withdrawal and forget changes use the existing
profile invalidation/repair path. A date passing does not change this historical
content's meaning or selection; it therefore needs no new temporal certificate
tables or activation/expiry scheduler.

Generated K pages follow the same principle: dated statements or clearly labelled
compilation-time snapshots, with existing freshness disclosure and mutation-driven
recompilation. They must not be treated as an authoritative current-state filter.
Routing or graph membership that explicitly asks what holds now evaluates the
single fact window at the requested time; `valid_until IS NULL` alone is not a
current predicate. A consumer requiring current truth uses fact retrieval rather
than inferring it from an undated cached sentence.

Do not call a completed tournament fact "no longer believed" because its event
day has passed. Render the date or period separately from any system withdrawal.

## 7. Source withdrawal, forgetting and recovery

Source withdrawal changes evidence currency and, where the existing lifecycle
requires it, fact belief status. It does not supply a world-time end. A withdrawn
article about 2022 does not establish that the event stopped happening on the
withdrawal day. Keep historical testimony according to the existing withdrawal
contract and record the actual system transition time.

Hard forget is different: deleted source content must not survive in facts,
prepared inputs, completed answers, adjudication payloads, evidence links,
profiles, K pages or derived indexes. Every new durable field belongs in the
existing deletion inventory and tests. No temporal-specific store is exempt.

When surviving evidence independently supports a date, ordinary adjudication may
retain it. Otherwise clear unsupported dates or remove the unsupported fact
according to the existing forget contract. Do not invent a replacement date.
Invalidate pending work and ensure late provider replies and retries cannot
restore erased payloads. Erasure completion must cover derived payloads, not only
the source row. Rebuild uses surviving claims and permitted recorded decisions;
it must not replay erased before-images. This requirement remains even though
the old per-endpoint compensation/checkpoint design is removed.

## 8. Existing stores and deployment

Stores created before this design hold fact dates whose meaning differs from the
chosen window: a null end there meant "still true" and the endpoints were often
copied from source time. Those values cannot be relabelled as world time, and
re-deriving them would mean replaying every retained claim through the new
adjudicator at ordinary model cost. This design does not convert such stores.
The schema migration refuses to run against a deployment that already holds
claims; the operator recreates the deployment and ingests its sources again.
This is a documented scope boundary, not a deferred feature: an in-place
conversion path was implemented, reviewed and then withdrawn on 2026-09-11
because no populated store needs it while the product is unreleased, and
carrying it added a serving fence, a second evidence-provenance mechanism and a
replay/verification surface to every writer and reader
([analysis](../analysis/mutable_fact_application_storage.md#no-existing-store-conversion-2026-09-11)).

The removed experimental migrations were never released. There is no automatic
downgrade from this schema either: a downgrade would drop the application
receipts and restore the old date meaning, so it raises instead. Disposable
test databases are recreated rather than downgraded.

## 9. Alternatives, costs and security

Permanent categories could make some rules cheaper, but require uncertain
classification to veto otherwise valid semantic identity. Two stored world
windows could make evidence-date unions cheaper to read, but introduce misleading
consumer semantics and another refresh/forget obligation. A separate correction
framework records detailed endpoint ownership, but ordinary mutable facts and
before/after adjudication history meet the chosen requirement without it.

[Current-only cached prose](../proposals/current_only_generated_summaries.md)
is a coherent alternative. It requires boundary
scheduling and stale-read protection, including delays and restarts. Date-qualified
profiles and snapshots are chosen instead because they retain useful history
without that wall-clock dependency. This is a product-contract choice, not a
claim that current-only caches can safely omit expiry.

The remaining costs are semantic calls for ambiguous identity, evidence reads,
short coordinated transactions, narrow application bookkeeping, and mutation-based
derived-content repair. Removing unconditional exact-match merging may increase
model use; measure it with the changed semantic generation and benchmark protocol.
Correctness is not conditional on a managed-cloud UI. All writers and deletion
enforcement stay inside the single-deployment engine. Published SQL roles keep
their existing least-privilege boundary; internal preparation/transcript data
does not become a public callable write surface.

## 10. Authority and supersession map

This document is the binding home for the revised fact-time contract. The old
documents and DDL remain marked as historical rationale, not parallel authority.

| Prior contract | Disposition |
| --- | --- |
| D107 T.0 canonical arithmetic and public SQL; T.4 claim vocabulary/full timestamp | Retained; already merged implementation is unaffected |
| D107 fixed fact kinds, dual windows, seed authority and kind/date identity gates | Replaced by §§2–3 |
| D106 date coercion and exact-text identity shortcut | Superseded where they prevent contextual identity (§3) |
| D110 staged writes and safe application requirements | Retained as properties; implementation framework and DDL replaced by §4 |
| D110 autonomous correction/compensation, discrepancies and endpoint ownership | Removed; ordinary adjudication (§3.1) |
| D110 current-only cache certificates and temporal event stores | Replaced by date-qualified profiles/K and explicit fact-time reads (§6) |
| D110/D113 temporal dependency/checkpoint and conversion schema | Withdrawn; deletion/recovery obligations remain (§§7–8) |
| D111 unknown-start state exclusion exception | Unnecessary without kind-based identity or universal overlap exclusion |
| D112 deterministic all-overlapping-state evidence attachment | Removed; support targets follow semantic identity, not interval overlap |
| D113 assertion provenance, atomic support movement and stable retry result | Retained as properties (§§3.2–4); its mandatory stores and proof graph are withdrawn |
| D43 fixed-period measurement no-cap rule | A known reporting period is the chosen finite world window. A later measurement does not automatically cap that period, and its end does not invalidate the belief (`invalidated_at` stays unchanged). Conflicting same-period figures still coexist; an open world window is not required to preserve historical belief. |
| D41 / D55 / D74 | Claims remain immutable; world time is separate from withdrawal; hard forget still covers all durable derived copies |

Existing general review/audit capabilities are not removed. This design adds no
dedicated date-review surface and makes no change to D108's autonomous authority
boundary. Consumer and storage implementation contracts must implement this
document, not resurrect superseded SQL because it remains in Git history.
