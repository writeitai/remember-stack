# Entity identity and retrieval — design (binding)

> **Binding D100 amendment (2026-08-31).** T4 is one joint call on the
> configured simple model over every candidate in the bounded snapshot. It
> returns one candidate id or `new`, preferring a compatible existing entity
> unless evidence positively distinguishes a new referent. The current
> generation has no frontier seat, confidence-routing branch,
> `insufficient_evidence`, or provisional mint. D99's snapshot/revalidation,
> T3 diagnostics, convergence, and recovery contracts remain binding.
>
> **Historical D99 amendment (2026-08-28; superseded in part by D100).** T4 distinguishes `same`,
> positively supported `different`, and `insufficient_evidence`. Only
> `different` creates an automatic cannot-link. An incomplete candidate or T4
> prefix cannot prove novelty; ingestion may mint a merge-eligible provisional
> fragment whose uncertainty remains in the append-only decision evidence.
> Current profile publication invokes bounded neighborhood convergence.
> Resolver provider calls run outside the lemma-lock transaction and commit
> only after locked state revalidation. Section 3 and the implementation/test
> contracts below contain the complete amended behavior.
>
> **Binding D98 amendment (2026-08-27).** Merge and identity changes are visible
> through survivor-normalized PostgreSQL graph views after commit; there is no
> P2 rebuild. The D95–D97 identity, profile, and retrieval behavior is otherwise
> unchanged.

**Status:** accepted as operator-directed 2026-08-26 and amended through D100
on 2026-08-31. Dual implementation review still applies before code lands;
this document is the *how*, not a phased MVP.
**Date:** 2026-08-26
**Decision log:** D95, D96, D97, D99, D100
**Analysis:**
[`entity_identity_and_retrieval_analysis.md`](../analysis/entity_identity_and_retrieval_analysis.md),
[`entity_resolution_uncertainty_and_convergence.md`](../analysis/entity_resolution_uncertainty_and_convergence.md),
[`d99_proposal_convergence_lock_convoy.md`](../analysis/d99_proposal_convergence_lock_convoy.md),
[`binary_match_biased_t4.md`](../analysis/binary_match_biased_t4.md)
**D99 review:**
[`Claude Opus — identity uncertainty`](../../design/reviews/REVIEW_claude_opus_d99_identity_uncertainty_2026-08-28.md),
[`Claude Opus — proposal coalescing`](../../design/reviews/REVIEW_claude_opus_d99_proposal_coalescing_2026-08-28.md)
**Amends:** [`registries_design.md`](registries_design.md) §2 profiles,
§3 cascade (T0 as verdict), §4 “How an entity gets its type”;
[`e2_e3_claims_relations_design.md`](e2_e3_claims_relations_design.md)
E3 EntityRef / signature-before-resolve;
[`retrieval_design.md`](retrieval_design.md) default neighborhood;
[`postgres_schema_design.md`](postgres_schema_design.md) `entities.type`
NOT NULL and `mentions.emitted_type` as required extract law.
**Does not amend:** D2 (claims vs relations), D3–D4 (supersession), D5
(`other:` escape remains), D43 (observations stay untyped), D48
(hydration), D60/D61 (library boundary).
**Operator direction (same day):** no entity type classes; profile text
(and the observations that feed it) replace type filters. **No
backward-compatible dual writer** (plan cutover).

Write for a cold reader (CLAUDE.md Rule 1). Sequencing lives in
[`entity_identity_and_retrieval.md`](../plans/entity_identity_and_retrieval.md)
(Rule 2).

---

## 1. The problem in one page

The system must attach every fact to the **right real-world thing** and
then **retrieve** those facts without asking the caller to speak
ontology.

Three failures the previous cascade cannot handle at once:

1. **Metonymy.** “SAP” as vendor shorthand and “SAP” as the suite in
   the next sentence are often **one retrieval identity**. Splitting
   them on type makes every lookup union two nodes.
2. **Homonymy.** A man and his father share a name. Both are people.
   Type cannot split them. The current T0 exact-lemma match glues them
   at confidence 1.0 before any judge or profile can speak.
3. **Dual role.** “Someone works for me” is one person, not a Person
   entity plus a Company twin. Domain/range `works_for: Person →
   Organization` either drops the fact or mints a second id.

The reason any of this exists is retrieval: resolve names in a question
to **ids**, load facts about those ids, hop a small neighborhood
**without requiring a predicate**, match **fact text**. Wrong ids make
every hop wrong. Required types and custom verbs make every request a
query planner.

---

## 2. Decisions this design implements

**D95/D100 — Identity is the referent; ambiguous residue favors reuse.** One `entity_id` per real-world
thing. A name generates **candidates**. **T0 never auto-merges** (exact
lemma only lists ids). T3 may accept a repeat when a profile exists;
T4 when the profile is empty, fights, or several candidates exist. The
same spelling may be two ids. Profile is T3/T4 evidence, never the
lookup key. Relatedness is a relation. No common-name list. Exact-lemma
auto-accept as a default-off “large corpus” switch is **rejected**; a
narrower opt-in (closed unique namespace, not entity count) remains an
[unchosen proposal](../../design/proposals/optional-exact-t0-accept.md),
not WP-I.5.

T4 compares the bounded candidate set jointly and returns one candidate id or
`new`. Compatible facts, missing overlap, and different topics favor an
existing candidate; `new` requires positive distinction from every supplied
candidate. Candidate limits bound work and remain visible in the decision
audit. Current profile publication still reconsiders the touched neighborhood
through D99 convergence.

**D96 — No entity types; extract names; profile is observation prose.**
Mentions are a diary of names (string, claim, span). E3 does not emit
a class. There is no `entities.type`, no hats table, no domain/range
on kinds, no D86 type gate. Dual-role facts (`works_for` pointing at
a person) are allowed because both ends are ids. What used to be
“Company” / “bank” / “lives in Italy” lives in **observations** and in
the **profile**, which is a cached prose (and salient-observation)
projection of those facts — not a classification.

**D97 — Default retrieval does not speak ontology.** The ordinary
question path is: resolve to ids → load observations and relations for
those ids → one-hop `neighborhood` with an **empty** predicate list →
match fact text (relation `fact_label`, observation `statement`,
claims). A governed predicate is an optional narrowing filter.
Observations are not graph neighbors; they are loaded as facts about
the id.

---

## 3. Identity cascade (amends D17 T0-as-verdict)

Keep T0–T4, block-loose / decide-tight, registry-self-contained (D20).
Change what T0 *means*.

### 3.1 T0 — candidate list, never a merge

Exact match on `aliases.normalized_lemma` **lists** matching active
**entity ids** (0, 1, or many). Count **distinct `entity_id`s**, not
alias rows: two provenances (`source` and `llm_canonical`) on the same
id are still one candidate.

**T0 never auto-accepts.** Same cleaned spelling is a clue, not a
verdict. No common-name census. No “distinctive lemma” shortcut.

- **0 candidates after an untruncated block** → **mint an authoritative
  cascade outcome**. This says the configured blocking pass surfaced no
  candidate; it does not prove that an entity outside T1/T2's recall ceiling
  cannot be the same referent.
- **1 or more** → those ids are the candidate set. Decision is T3 or one joint
  T4 call (§3.1.1). T4 selects one supplied candidate or `new` (§3.2).

Candidate generation returns both the bounded candidate tuple and
`search_complete`. Loading `limit + 1` rows is sufficient to prove whether the
returned `limit` is a prefix. T4 receives every candidate in that bounded
snapshot in deterministic T3-relevance order. Limits remain strict and
completeness remains audit evidence; neither creates a third decision outcome.

A thousand mentions of the same James are **not** a thousand T4 calls.
Repeats are T3 once a profile exists.

### 3.1.1 Who actually decides (T3 cheap, T4 residue)

| Situation | Verdict | Why |
|---|---|---|
| No candidates after an **untruncated** block | Authoritative cascade mint | No LLM; the configured blocking pass surfaced no candidate, without claiming perfect blocking recall |
| One candidate, **profile exists**, mention+claim embedding sits on that profile (T3 accept band; starting threshold from D22, not a folklore constant) | **Accept, no T4** | Repeat “James” after we know him |
| One candidate, **empty profile**, or profile **fights** the claim, or **several** exact candidates | **T4** | Father/son, second employee, first clash, thin identity |
| T4: `match(candidate_id)` | That supplied `entity_id` | Compatible or strongest existing referent; ambiguity favors reuse |
| T4: `new` | Authoritative cascade mint | Evidence positively distinguishes the incoming referent from every supplied candidate |

T3 embeds **mention (name + this claim) against the candidate’s
profile** (name + summary + salient facts), never name-only vs
name-only. Empty profile is **fail-safe**: do not treat high name-only
cosine as a T3 certainty — send it to T4. When the bounded T4 evidence is
compatible but inconclusive, the binary policy deliberately prefers a matching
existing identity over a new fragment.

This is how father/son becomes possible (they reach T4 with different
facts) **and** how a clean table stays affordable (repeats of a known
person are embeddings, not judges).

**Not the default, not WP-I.5:** an operator flag that turns exact-lemma
auto-accept back on. A large store has **more** `Jan`s, not fewer
(birthday paradox). Enabling the old exact-hit *because* the corpus is
large is backwards. The cheap path for repeats is T3+profile, not
resurrecting T0-as-verdict. The idea of keeping exact-hit as a
**manual, default-off** switch is recorded as an unchosen proposal
([`optional-exact-t0-accept.md`](../../design/proposals/optional-exact-t0-accept.md));
its adoption trigger is a closed unique namespace (SKUs, employee
numbers), **not** entity count. Identifier-shaped T0 (email, LEI,
ORCID) is a different future path, not name-lemma auto-merge.

### 3.2 Same lemma, two ids—or one selected candidate

T4 receives every member of the bounded snapshot together. It returns
`match(candidate_id)` only for an id present in that snapshot, or `new`.
Compatible but topically different facts are not positive distinction. When
several candidates remain compatible, the first candidate in deterministic
T3-relevance order wins. This makes the tie policy explicit and prevents model
or row-order noise from minting another active identity.

`new` means the supplied evidence positively distinguishes the incoming
referent from every candidate shown to T4. It mints an authoritative
`entity_id` with the same canonical spelling and a new alias row. The lemma
advisory lock serializes the commit; it does not forbid a second row.
`resolution_exclusions` may record the supplied candidate pairs so clustering
does not immediately re-propose the positively distinguished identities. Each
effective automatic row names its supporting append-only decision and resolver
version. Human rows name `basis=human`.

The decision audit retains `search_complete`, the ordered candidate ids, every
T3 score or gate, the selected candidate or `new`, model, and rationale. The
current generation has no provisional authority. Historical D99 provisional
and tri-state rows remain append-only evidence and are never rewritten.

The D99 migration classifies every pre-D99 `created_by=auto` row as
`basis=legacy_binary, is_effective=false`; it remains audit evidence but cannot
block convergence because the old schema could not prove whether “not match”
meant difference or uncertainty. Existing human rows become effective
`basis=human`. A later supported `different` or human verdict may revalidate the
same canonical pair by updating its basis/support pointers; a later superseding
decision may retire it with `is_effective=false`, `retired_at`, and the retiring
decision id. Clustering reads only effective `supported_different` and `human`
rows. The append-only decisions retain both the original and later evidence.

Father/son and two employees in different cities are this path. SAP
shorthand stays one id because T3/T4 see one referent, not because T0
auto-merged the string.

### 3.3 Profile is T4 evidence (amends registries §2 as implemented)

The profile is **not** a type, a label, or a second identity. It is a
**cached projection of the entity’s important observations** (and
salient relations), written as short prose plus the statements it was
built from.

- **Truth stays in observations** (D43): “KB is a bank licensed by
  ČNB”; “based in Italy”; “lives in Prague.” Those rows are the
  join table. There is no parallel `entity_types` / hats vocabulary.
- **`profile_summary`** is the refresher’s blurb over that join
  (debounced on evidence change), e.g. “Czech commercial bank; seat
  Prague; ČNB licence.” Until any observation exists, T4 sees claim
  text + candidate name only.
- **Salient observations** (starting point: a small evidence-ranked
  set for the candidate) are passed to T4 with the blurb so “is a
  bank” / “lives in Prague” is visible even if the blurb is stale or
  thin. This *is* the profile mechanism, not a later add-on.
- “List banks” is fact/profile **text** retrieval over those
  statements, not a type filter. Boolean lists are only as sharp as
  the observations. That is accepted.

The as-built starting refresher is deterministic: it evidence-ranks a
bounded set of supported, open-ended observation statements and canonical
relation prose (`valid_until IS NULL`, a deliberate stable projection so a timed
fact cannot expire without an evidence event to trigger refresh), joins the leading statements
into `profile_summary`, and
embeds `name + summary + salient facts` under `entity-profile-v2`. It is
composed synchronously after evidence add/recount/supersession, merge/un-merge, terminal human
review, normal deletion, and the D74 lineage scrub. Survivor profiles aggregate the complete
redirect closure. Merged members retain separately attested member-local profiles solely for
joint neighborhood re-decision; retired rows clear, and public resolution/search still exposes
only active survivors. Deployment setup keyset-
backfills every active or merged entity after a policy-cut migration and republishes the
entity semantic channel only after that resumable pass completes. The exact
text hash debounces unchanged evidence. Resolution
reconstructs the expected summary/hash from current facts; stale or missing
attestation disables T3 and T4 still receives the current salient statements.
Clustering performs the same exact current-input check under evidence locks before reading a
generation-pinned vector, so stale or missing member state cannot authorize a merge or split.
Merged-member relation prose keeps endpoints inside the member subtree named as that local profile
root and preserves raw canonical names outside it, rather than rewriting a sibling to the shared
outer survivor and self-reinforcing an earlier merge.
Hot-path redirect traversal uses recursive CTEs anchored at the requested ids rather than the
deployment-wide survivor view. No queued stale snapshot can overwrite newer evidence: the
refresher snapshots under the identity/evidence locks, releases the transaction for the provider
call, then reacquires the locks and rebuilds the exact input. A changed hash discards and meters
the stale vector and triggers a bounded retry rather than writing it. Multi-entity refreshes use
bounded provider batches, but each entity still revalidates and commits independently. Salient
facts use index-backed per-member/per-endpoint prefix scans before the final bounded entity rank,
so a hub cannot turn profile refresh into a deployment-wide fact sort under lock. Worker-side
contention exhaustion leaves the stale cache empty and completes already-durable evidence work;
the later evidence mutation owns another refresh, preventing paid normalization replay and DLQ.
Setup and operator surfaces continue to surface exhaustion as a failure.

Merge application also resolves a queued survivor to its live terminal root and requires the
absorbed target to remain active. A target that joined another cluster after proposal creation is
rejected for re-evaluation rather than silently extending the stale proposal's blast radius.

Worker refresh calls retain their processing-attempt meter. Setup backfill, human-review, and
hard-forget readiness replay have no worker identity, so they write provider receipts to the
operational surface-cost ledger instead of disappearing between ledgers.

Production `_T4_PROMPT` includes:

```text
MENTION: {name}
CLAIM CONTEXT: {claim_text}
CANDIDATES IN RELEVANCE ORDER:
- candidate id, canonical name, aliases, profile description,
  current salient facts, and T3 score or gate

Choose exactly one result:
- match one supplied candidate id; or
- new, only when evidence positively distinguishes the incoming referent from
  every supplied candidate.

Prefer an existing compatible candidate. Missing overlap and different topics
do not establish a new identity. If several candidates remain compatible,
choose the first candidate in the supplied relevance order.
```

T4 answers **match / new** only. It does not emit `related_to`.
Father/son, if the claim states kinship, is a **relation** written
after both ids exist, not a merge.

T3 compares the mention embedding to the candidate’s **profile
embedding** (name + summary + salient facts), not the name-only
vector stamped at mint. Two people with the same given name must not
share a vector because they share a spelling.

City, job, employer, “is a bank” **update observations and then the
profile**. They do not change `entity_id` and do not mint a type.

### 3.3.1 Profile publication is the convergence boundary

Every successful current-profile publication nominates the refreshed entity's
distinct alias lemmas for bounded `recluster_neighborhood`. The production
composition invokes that existing clusterer after relation-profile refresh and
after observation-profile refresh. The latter runs in an entity observation
flush only after every claim in that document version has finished relation
normalization (the **claim-normalize barrier**, D88/D90), so the refresh sees
the entity's globally ordered staged observations rather than a partial claim
prefix. A later fragment refresh therefore sees every earlier current peer.
Duplicate
nominations are idempotent: an already-live equivalent merge is a no-op. A
merge-review proposal uses a deterministic identity over deployment id, sorted
live root entity ids, and the cluster-configuration fingerprint. Replaying the
same member set/configuration therefore conflicts on the same `review_id` and
does not queue twice. If the live member set changes, the new identity is a new
proposal; in the same transaction it marks overlapping pending proposals
`auto_resolved` with a supersession note naming the replacement. An accepted or
rejected historical proposal is never rewritten.

Convergence keeps D21/D24 safety. Missing or stale member profiles cannot merge
or split. Automatic merge remains disabled without an accepted calibration;
otherwise qualifying small pieces may use the configured reversible auto path.
Any component above the blast-radius cap produces one cluster review proposal.
Human-confirmed and positively supported `different` exclusions remain hard
cannot-links. Uncertainty never creates one, so it cannot poison later repair.
When automatic merge is disabled, the proposal-only pass holds the deployment
identity epoch in shared mode: it cannot merge or split identities. Repeated
nominations for the same normalized lemma are coalesced by a nonblocking
neighborhood advisory lock. The pass also tries, rather than waits for, every
member evidence lock; one busy member makes that nomination finish without a
proposal. A later successful profile publication nominates the neighborhood
again. This is safe because a report-only pass cannot change identity, while a
proposal is useful only when every compared profile is from one coherent
snapshot. It also prevents a large provisional component from making ordinary
profile workers queue behind a proposal that is waiting for one slow provider-
backed observation transaction.

A pass authorized to auto-merge retains the global blocking neighborhood lock,
the exclusive identity epoch lock, and blocking member evidence locks because
it may split or merge identities. Both modes consider the union of member
identity closures in one global entity-id order before reading profile inputs,
matching the profile publisher's closure order and preventing cross-closure
advisory-lock cycles. Proposal-only passes may run for different lemmas; if
their blocking neighborhoods overlap, the sorted nonblocking evidence locks
allow one coherent pass to proceed and make the other defer without a cycle.

This is local convergence, not a deployment sweep: gather starts from the
touched alias lemma, applies existing blocking reach and black-hole bounds, and
records a report. Failure to converge does not roll back already-durable facts
or profiles. The ordinary worker retry replays the idempotent nomination; an
operator can inspect or replay queued proposals. Merge and unmerge continue to
refresh affected profiles through the non-recursive base refresher so a merge
does not recursively trigger itself.

Profile publication has its own bounded identity/evidence lock wait. A
PostgreSQL statement timeout whose cause is specifically an advisory-lock wait
is translated to `ProfileRefreshContendedError`; it is not a generic database
failure. Evidence workers have already committed the authoritative fact change
before this disposable projection step, so they record the typed contention,
leave the projection fail-closed, and complete without replaying paid
normalization or adjudication. A later evidence mutation owns another refresh
attempt. Review, forget, and other callers that require synchronous repair may
let the typed contention propagate and retry their operation. Query timeouts,
connection failures, and all other database errors remain failures.

### 3.3.2 Resolver provider calls do not hold the lemma transaction lock

The normalized-lemma advisory lock remains the database-enforced serialization
point for identity writes. Resolution uses an optimistic three-part operation:

1. lock briefly, load the bounded candidate/profile snapshot and completeness,
   fingerprint it, then commit;
2. run T3 embedding and T4 generation with no database transaction held; and
3. lock again, reconstruct the same snapshot, and commit the mention/decision,
   exclusions, aliases, and optional mint only if the fingerprint is unchanged.

A changed fingerprint discards the stale provider result, records its cost, and
retries from step 1 up to a bounded attempt count. Exhaustion raises a typed
retryable contention error; it never commits stale identity authority. The
worker ledger then applies its ordinary persisted backoff and attempt budget,
which is distinct from the resolver's short in-call retries. Repeated contention
can still end visibly in DLQ after that outer budget; the contract is explicit
replay without manual worker-count changes, not an impossible guarantee that
contention can never dead-letter. The
fingerprint covers candidate ids/order, completeness, canonical names, current
profile summaries and salient facts. Adding, retiring, merging, or refreshing a
candidate therefore invalidates an in-flight decision. This is the same
snapshot/unlocked-provider/revalidation pattern used by `ProfileRefresher`.

### 3.3.3 T3 outcome diagnostics

Every final resolution decision records one bounded T3 outcome and the candidate
count. The allowed outcomes are `accepted`, `below_threshold`,
`multiple_candidates`, `profile_missing`, `profile_stale`,
`embedding_missing_or_wrong_generation`, and `embedding_hash_mismatch`. A
candidate may carry its own gate in the audit JSON, but aggregate telemetry uses
only this fixed vocabulary; entity ids and names are forbidden as metric labels.
“Skipped” and “evaluated below threshold” are distinct.

Several candidates continue to route to T4 under D95. A unique-top/margin T3
policy is not silently introduced: it requires a D22 curve covering both
same-referent positives and father/son or same-name-colleague negatives, followed
by an explicit D95 amendment.

**Forget (D74).** Hard-forget is lineage-scoped: a fact evidenced by
another remaining document stays. The **profile** is a derived cache, so
forgetting document A on a **shared** entity must **invalidate and
recompute** that entity’s `profile_summary`, salient-observation set, and
profile embedding from remaining evidence (or clear until recomputed).
Scrubbing only `exclusive` entity ids leaves A’s distinctive phrase in
the blurb. Queued refresh work whose inputs included A must be rejected
as stale. Verification: after forget, the forgotten phrase is absent
from summary, salient inputs, vector attestation, and profile/fact
search.

### 3.4 `judge_pair`

The golden-pair harness must **not** return true solely because lemmas
are equal. Same-name non-matches are first-class eval cases. Without
that, D22 cannot see the failure this design exists to fix.

Thresholds are **one global** precision/recall curve (types are gone).
The harness still **records the deciding tier** (T0 / T3 / T4) so a
false merge can be blamed on the right step. “One curve” does not mean
deleting per-tier diagnostics. A passing run must measure both positive
and negative labels and at least one same-lemma/T0 negative canary,
identified from the normalized surfaces rather than the optional expected-tier
annotation; any false merge of that canary blocks the run independently of
the global precision floor, so easy positives cannot dilute the D95 failure. A
same-lemma golden pair may exercise T3 only when both stored contexts
provide distinguishing evidence; an empty-evidence pair skips unsafe
name-only cosine and reaches T4. Activating D95 T0 requires a recorded
passing run of this suite **after** profile/T3 safety exists.

### 3.5 Relatedness

Father/son, SAP SE ↔ S/4HANA, employer/employee: **relations** with a
predicate and valid-time. Not `related[]` on the entity. Retrieval
walks them (D97).

---

## 4. Extract and mentions (amends E3 EntityRef)

### 4.1 What a mention is

The `mentions` table stays: immutable transcript of “we tried to link
this name in this claim.” Columns that remain load-bearing:
`surface_form`, `normalized_lemma`, `canonical_name_form`, `claim_id`,
`chunk_id`, `doc_id`, spans when known.

Do not write `emitted_type`. The column, if left in the schema, stays
NULL and unused. Mentions are names, not classifications.

Mentions are not candidates. Do not SELECT mentions to resolve a name.
Do not add `mention_id` to evidence tables.

### 4.2 EntityRef

E3 structured output for an endpoint has **no type**. “Name-only” means
no class, not “drop the spelling that appeared in the claim.”

The payload carries:

- **`name`** — canonical nominative form (feeds T0 / `llm_canonical`
  alias), and
- **`surface`** — the span as it appeared in the claim when it differs
  (`App` vs `Application`). If they are the same, `surface` may equal
  `name` or be omitted.

Resolver writes `surface` as `mentions.surface_form` and as a
`provenance=source` alias, and `name` as `canonical_name_form` /
`llm_canonical`. Without `surface`, WP-I.1 cannot record `App` and
`Application` as one id. The prompt has no registry-types block. D86’s
unknown-type path is vacated (D96). Unknown **predicates** still follow
D5 (`other:` or drop).

### 4.3 Eligibility — do not mint filler nouns

The normalizer must not emit a name that is only a bare head noun
(`game`, `app`, `system`, `card`, `photo`, `module`, `the system`)
unless the claim qualifies it as a specific referent (FIFA 23,
James’s Unity strategy game). Prefer dropping the relation or
observation over inventing a referent. Protocol/boilerplate lines
(adapter banners, “X is a participant”) are the same class.

This is extract eligibility, not a new mentions subsystem.

### 4.4 Aliases

On mint **and** on match, write the surface form actually seen in the
claim as `provenance=source` in addition to `llm_canonical`. `App` and
`Application` can be one id. Do not alias `game` onto FIFA 23 because
they co-occur.

### 4.5 Shared lemmas

A lemma that points at several entities is **not** demoted (D102). Blocking
ranks by match score, then by how closely the entity's own canonical name
resembles the query, then by `created_at`, then by `entity_id` — so a
complete tie resolves oldest-first, never by row identity. Sharing a name costs a candidate
nothing, because sharing a name is the ordinary case the cascade exists to
adjudicate — under D95 the resolver itself mints a second row for one real
person pending adjudication, so a shared lemma is as often our own
conservatism as it is two different people.

D21's promiscuous-signal concern (`info@company.com`, placeholders) is
carried by the mechanisms that *decide* identity — T3 profile evidence, T4,
and `resolution_exclusions` cannot-link edges — not by ranking candidates
down before anything examines them. T0's role is unchanged: exact T0 always
lists candidates and never accepts a referent.

---

## 5. No entity types (amends D18 typing and domain/range)

There is **no** class on the entity, **no** hats table, **no** extract
type, **no** domain/range over kinds.

“Company”, “bank”, “court”, “based in Italy” are **facts** (observations
or relation `fact_label`s). They appear in the profile because the
profile is a projection of those facts (§3.3). They are not a second
ontology.

### 5.1 Dual role

“Someone works for me” is two resolved ids and `works_for`. The object
does not have to be an Organization. A separately **named** company
is a second entity plus a relation. Signature tables (`predicate_signatures`,
`_signature_allows`) are removed. Predicates remain a **name vocabulary**
(D5): governed list plus `other:`.

Travel, residence, “is a bank”: observation and/or a generic edge with
a fact sentence. Do not invent a core verb per noun, and do not invent
a type per sector.

### 5.2 What remains of D15 / D18 / D86

- **D5 predicates** stay (governed + `other:`). Retrieval filters on
  them are optional and, when used, may name any stored predicate
  including `other:traveled` (D97).
- **D18 entity types and domain/range** are withdrawn as identity and
  as a write gate. Extension packs that only added types are unused;
  packs may still add **predicates**.
- **D86** (unknown entity type retry-then-drop) is vacated: extract
  does not emit types. Unknown predicates still map to `other:` or
  drop.

### 5.3 Documented non-goals

- M2M hats / Graphiti labels on the entity (optional kind filters).
  Kind browse (“list companies”) is observation/profile text until a
  later **facet** design exists. That facet would be derived from
  observations, not a return of `entities.type`.
- Hats on facts as a second vocabulary beside predicates.
- `(name, type)` uniqueness.

---

## 6. Predicates, relations, observations

Relations stay `(subject_entity_id, predicate, object_entity_id)` plus
bi-temporal metadata and `fact_label`. Observations stay untyped
statements on one `subject_entity_id` (D43). Evidence stays claim →
fact. **No schema change** to those fact tables for types or mentions.

Governed predicates remain a convenience vocabulary for **optional**
filters (`works_for`, `knows`, `reports_to`, …). The set of **legal**
filters is **dynamic**: any predicate that exists in the store,
including `other:`. Named recipes may still bind a common verb. Default
retrieval does not require a predicate (D97). Do not promote
`other:traveled` into core just because it is common.

Unlabeled `related_to` with **no** fact text is rejected: one hop
becomes a hairball and “who works here” becomes an LLM filter over
noise.

---

## 7. Retrieval (D97)

Ordinary question path (zero extra LLM beyond whatever `resolve` already
does on the hot path — T0–T3 only, D17):

1. `resolve` the names in the question to ranked `entity_id`s. Ambiguity
   returns ranked candidates, never a silent guess (existing S51).
2. `lookup` relations **and** observations for those ids (no predicate
   required).
3. `graph.neighborhood(entity_id, hops)` with **empty** `predicates`
   (already the code default: walk every `RELATES` edge). Cap hops
   (starting point: 1 for ordinary questions, existing default 2 is
   allowed when the recipe asks). Observations are **not** neighbors;
   they came from step 2.
4. `search` fact text (relation labels, observation statements, claims)
   constrained to those ids / neighborhood ids when ids were supplied.
5. A predicate is an **optional** argument when the question is
   clearly that filter (“who reports to X”). Any stored predicate is
   legal, including `other:`. Do not require `type` on `resolve`.

`resolve_entity` candidate metadata exposes ambiguity; it is not testimony or
fact content. The library returns that distinction and cannot compel an
arbitrary external agent's next action. The repository's LoCoMo answer loop
mechanically enforces its own content-before-`Unknown` rule: after an
identity-only lookup it requires one bounded testimony, fact, or combined
context attempt without silently choosing one candidate. That harness policy
improves benchmark degradation while leaving library ambiguity explicit and
does not substitute for identity convergence.

The default operation has one absolute 25-second PostgreSQL budget. Pool
admission, P1 channel-readiness checks, semantic fact nomination, optional
entity-profile rescue, anchor/neighbor authority checks, graph expansion, and
fact/evidence confirmation all consume that same deadline. P1 reads and fact
authority share one bounded admission object over a dedicated no-overflow
interactive-retrieval engine; they must not use separate semaphores over one
physical pool or fall back to the general worker/write pool. Every admitted P1
or fact transaction derives both `statement_timeout` and
`transaction_timeout` from the remaining budget. Saturation or expiry returns
a typed `boundary`; it never silently widens scope or falls back to anchor-only
retrieval.

Assured operations (`fact_context`, `answer_context`, D87) follow the
same default: do not require a predicate list to return an entity’s
facts and one-hop relations. Because this changes the pre-D97 selection
semantics, parameters, and entity bound, the canonical descriptors are
`fact_context@2` and `answer_context@2`. The unaffected
`resolve_entity` and `testimony_context` descriptors remain version 1.
Primary P1 fact-channel unavailability returns a typed `boundary`; it
must not escape as an untyped HTTP 500 or widen to another authority.

Clean neighborhoods are part of this contract. Filler-noun entities
and duplicate-me Company twins make hops wrong even when the walk is
untyped.

---

## 8. Golden set (D22)

Same-name and dual-role cases are first-class. Measure **false merge**
vs **false split** per tier, including T0.

Minimum rows:

| Case | Expected |
|---|---|
| “we installed SAP” / “SAP announced…” | one id |
| SAP SE vs S/4HANA, both named | two ids + relation |
| Java language vs Java island | two ids |
| Father and son, same name, different lives | two ids + kinship relation if claimed |
| One person moved city | one id; profile updates |
| Two employees, same name, different sites, extra evidence | two ids |
| “Someone works for me” (I am a person) | one id for me; relation allowed |
| Bare `the system` / `game` | no entity |
| James traveled to Italy as a sentence | observation and/or generic edge; Italy is an entity **only** if nominated as a name, not because travel must be `located_in` |
| “KB is a bank”; “based in Italy” | one id; facts on observations; profile repeats them; “list banks” hits that text, not a type |

`judge_pair` must be able to score the same-name **non**-matches.

---

## 9. Implementation-facing contracts

| Site | Contract |
|---|---|
| `EntityRef` | canonical `name`; `surface` when the claim spelling differs; **no type** |
| `CascadeResolver.resolve` | snapshot candidates + completeness under the lemma lock; decide outside the transaction; re-lock/revalidate before writing; bounded contention retry |
| candidate result | load `limit + 1`, return bounded candidates plus `search_complete`; order the bounded set by T3 relevance before T4 |
| `T4Selection` | exactly `match(candidate_id)` or `new`, plus confidence/rationale used only for audit; selected ids must belong to the supplied snapshot |
| `_T4_PROMPT` | one joint configured-simple-model call with the bounded incoming claim and every candidate's deduplicated aliases (starting cap: 20), current profile description, salient facts, and T3 score/gate; explicit match bias |
| resolution decision features | candidate completeness/order, every T3 score/gate, selected candidate or `new`, rationale/model, and one bounded T3 outcome reason; no current provisional authority |
| resolution decision method | keep `T4_small` for the configured simple-model seat; `resolver_version` and features distinguish D99 pairwise rows from D100 joint rows; retain `T4_frontier` only for historical readability |
| `resolution_exclusions` | a T4 `new` may write supported-different rows only for candidates supplied to that joint decision; historical D99 and human rows retain their existing basis/effectiveness rules |
| T3 upsert | embed name+profile (+ salient facts) when they exist |
| Profile refresher | deterministic current-fact projection under `entity-profile-v2`; rewrite `profile_summary` + vector attestation from remaining observations/relations; D74 shared-entity forget recomputes, not exclusive-id scrub only |
| convergence composition | after a current relation/observation profile refresh, nominate distinct touched alias lemmas to the existing bounded clusterer; deduplicate equivalent open review proposals |
| `_INSERT_MENTION` | no `emitted_type` |
| `_INSERT_ENTITY` | no `type` column |
| `_signature_allows` | removed |
| `GraphQueries.neighborhood` | empty predicates = all `RELATES` (keep) |
| `judge_pair` | lemma equality is not automatic match |
| E3 prompt | names + governed predicates; no REGISTRY TYPES; bare-noun refusal |
| `resolve` primitive | drop `type?` |
| LoCoMo answer loop | reject terminal `Unknown` after identity-only reads; require one content-bearing testimony/fact/context attempt; retain the existing bounded invalid-completion retry |
| P3 Tier 1 | `entities/<entity_id>/` — not `entities/<type>/<entity_id>/` (`e0_files_design.md`) |

**Type-cut consumer checklist** (same PR as the schema drop; not
compatibility): `workers/p2.py`, `spine/projection.py` Entity export,
`workers/p3.py` + P3 path above, P1 entity search, `memory_v1.entities_current`,
`GraphNode` / envelope, `query_engine` `resolve`/`predicate_absence`,
`http_api.py` / `sdk.py`, `assured_operations.py`, bootstrap/`core_manifest`
type seed, eval type strata, migration tests. Dropping the SQL column
without these still fails the hard cut.

Schema: drop `entities.type` NOT NULL (column dropped or unused). Drop
or stop writing `mentions.emitted_type`. `predicate_signatures` unused.
`entity_types` unused for identity (may remain as dead registry until
migration removes it). Migrations amend `postgres_schema_design.md` in
the same implementation PR. Re-ensure the live property-graph catalog and
rebuild P3. Existing type values are
**not** migrated into hats; they are discarded as identity. If a fact
“X is a bank” exists, it is already an observation.

**Cutover:** no mixed-version writer contract. Schema drop and name-only
mint ship together (migration first in that PR). Old normalize
generations are abandoned, not dual-run. Sequencing:
[`entity_identity_and_retrieval.md`](../plans/entity_identity_and_retrieval.md).

---

## 10. Alternatives (complete-system, not phases)

| Alternative | Why it lost |
|---|---|
| Keep T0 exact as verdict | Homonyms impossible |
| Keep D99 tri-state T4 and provisional mint | Honest uncertainty still creates another served identity and makes the next resolution harder |
| Keep pairwise small→frontier T4 | Up to three isolated calls cannot compare the candidate set jointly; confidence routing adds a model seat without adding evidence |
| Binary T4 with a neutral prompt | A forced but unspecified tie becomes model or candidate-order noise |
| Remove candidate limits | Unbounded provider work and prompt growth are not a scale contract |
| Add a second unresolved identity store | Adds another serving state when the complete-system match-biased policy can make one bounded operational decision |
| Distinctive lemma + common-name stoplist | Second `Jan` still glues; thousands of locale-dependent names; uniqueness is a table property, not a name property |
| Exact-T0 auto-accept as a default-off flag, enabled once the corpus is “large” | Large stores have more collisions, not fewer; T3+profile is the scale path. The flag itself is an [unchosen proposal](../../design/proposals/optional-exact-t0-accept.md) whose trigger is **not** entity count |
| `(name, type)` unique | Forks SAP |
| Required extract class | Error space; first-mint law |
| D18 pre-resolve on emitted types | Not independent |
| `works_for` Organization-only | Dual-role second id |
| Delete mentions | Lose naming transcript |
| Unlabeled edges, no fact text | Hairball |
| Optional M2M hats / Graphiti labels | Kind filter without paying D18; still a parallel ontology; “bank” is a fact |
| Hats on facts | Duplicate of predicates + observation text |
| Keep types for “list companies” | Enumerative lists come from observation/profile text; facets if ever added are derived from those facts |
| Description as identity key | Move city → new person; profile is evidence, not the key |
| Hold the lemma lock across provider calls | Serializes unrelated database work behind network latency; snapshot and revalidation preserve commit correctness with a short critical section |

---

## 11. Test battery (acceptance; numbers are starting points)

- T0 never auto-merges: second `Jan` with empty profile goes to T4, not
  T0 accept. Repeat `James` with a profile can T3-accept without T4.
  T4 sees both candidates jointly when several exist.
- Compatible same-name evidence with different topics selects an existing
  candidate. Explicit father/son and same-name-colleague evidence selects
  `new`.
- Exactly one T4 generation call occurs per residue decision. The current
  resolver has no frontier call, confidence-routing branch,
  `insufficient_evidence`, or provisional mint.
- T4 receives every candidate in the bounded snapshot with aliases, current
  profile description, salient facts, and T3 score/gate. Its selected id must
  be one of those candidates; `new` records supported-different exclusions
  only for the supplied set.
- Candidate truncation records `search_complete=false` while still producing
  one binary selection; it does not create a third authority state.
- Resolver provider calls occur with no lemma-lock transaction open; a
  candidate/profile mutation before commit invalidates and retries the result.
  Exhausted in-call retries enter ordinary work-ledger backoff and remain
  replayable if the outer attempt budget reaches DLQ.
- Every non-T3 decision records why T3 did not accept; multiple candidates,
  stale profiles, missing/wrong-generation vectors, hash mismatch, and an
  evaluated below-threshold score remain distinguishable.
- A current profile refresh invokes bounded convergence. Equivalent replay
  neither duplicates an open cluster proposal nor repeats a completed merge.
- T4 sees profile **and** salient observations; two same-name vectors
  differ once profiles differ; “lives in Prague” can split homonyms.
- `judge_pair` false-merge/false-split on §8 rows.
- E3: `game` not minted; `FIFA 23` may mint; `EntityRef` has no type;
  source `App` + canonical `Application` become two aliases on one id.
- `works_for(Alice, Me)` persists with no types on either end.
- Profile refresher: bank + Italy observations appear in
  `profile_summary`; “list banks” can match that text.
- Neighborhood with no predicates returns `other:traveled` neighbors;
  observations for the same id load via lookup, not as graph nodes.
- One captured deadline reaches P1 readiness, fact/profile nomination, graph,
  and authority confirmation; a saturated retrieval slot returns a typed
  boundary before that deadline, and no unguarded checkout occurs.
- A lemma linking many entities is ranked on merit, never demoted (D102).
- Forget: exclusive entity still fully purged; **shared** survivor
  profile no longer contains the forgotten document’s distinctive
  phrase (D74).
- The benchmark answer loop cannot accept `Unknown` after only
  `resolve_entity`; one bounded content-bearing read is required. Existing
  malformed-answer retries remain bounded and accounted.

No LLM on the query path is added (D9).
