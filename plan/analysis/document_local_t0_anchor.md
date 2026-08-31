# Document-local exact T0 after a T4 match

**Date:** 2026-08-31

**Status:** analysis; non-binding evidence for D102

## Question

D100 made T4 converge repeated names, but a repeated canonical name can still
pay for the same model judgment hundreds of times. Once T4 has matched a name
to an existing entity in one document, should later exact occurrences in that
same document reuse the result without another model call?

This is not source/file attribution. The only source boundary used here is the
existing catalog `doc_id`. The parallel attribution work may later provide a
stronger source-local identity, but D102 does not depend on or modify it.

## Evidence

The completed v0.8.1 D100 `conv-26` run drained 1,386 work items with no final
failure or dead letter. It produced 24 entities rather than the 610 active
entities in the earlier D99 run, including one Caroline and one Melanie. The
D99 baseline and its immutable artifacts are documented in the cloud program's
[`locomo-conv26-d99-validation-2026-08-28.md`](https://github.com/writeitai/ultimate-memory-cloud/blob/8c23b89fca1a492401875bfd24f5702494a0de77/design/analysis/locomo-conv26-d99-validation-2026-08-28.md)
(retrieved 2026-08-31).

An operator SQL audit of the retained v0.8.1 store on 2026-08-31 grouped
current decisions by `(doc_id, normalized canonical name)`. The canonical name
was read from `mentions.canonical_name_form` and normalized with a lower/trim
approximation for this cost estimate; production uses the resolver's
`normalized_lemma` function.

| Diagnostic | Result |
| --- | ---: |
| Current resolution decisions | 1,001 |
| Committed T4 decisions | 983 |
| Paid T4 attempts, including contention retries | 1,015 |
| Document/name groups | 68 |
| Groups containing T4 | 58 |
| Committed T4 decisions avoidable after one T4 per group | 925 (94%) |
| Caroline avoidable committed T4 decisions | 517 |
| Melanie avoidable committed T4 decisions | 396 |
| Total processing cost | $3.431599 |
| T4 cost | $0.251951 |
| Estimated gross saving | about $0.22–$0.24 (roughly 7%) |

The estimate is an upper bound, not a measured post-change result. Concurrent
first occurrences may still make a paid call before revalidation observes the
anchor, and another corpus will have a different repetition pattern.

The same audit found no document/name group mapped to more than one entity in
this run. It did find several **cross-name** T4 errors such as
`Melanie's son` → `Melanie` and `Caroline's painting` → `Caroline`. Exact
canonical-lemma reuse would not create those matches because the names differ,
but it can repeat a wrong first T4 match. The first T4 decision therefore
remains the quality boundary.

## Alternatives

| Alternative | Disposition | Reason |
| --- | --- | --- |
| Keep one T4 call for every repeated mention | Reject | Auditable but repeats the same source-local judgment and cost. |
| Accept every globally exact T0 hit | Reject | Recreates D95's homonym failure across unrelated documents. |
| Accept document-local T1/T2 hits | Reject | Fuzzy and phonetic reachability is not identity; possessives and nearby names make the risk concrete. |
| Cache the result in process memory | Reject | Lost on retry/restart, invisible to other workers, and not forget-safe. |
| Join partitioned mentions and decisions on every repeat | Reject | Either the entity history or document history is unbounded, and the lookup would run while the lemma lock is held. |
| Durable document/entity binding projection | Choose | One primary-key lookup is bounded; append-only decisions remain authority and a missing/unready projection falls back to the ordinary cascade. |

## Proposed contract

The key is `(deployment_id, doc_id, normalized canonical name)`. A document is
the existing catalog lineage, so an unchanged or revised version of the same
document can reuse the judgment. A different document cannot.

Every D102 decision records a `document-t0-v1` coordinate (`doc_id` plus the
canonical lemma) in its existing JSON features and transactionally upserts a
`document_entity_bindings` row for the entity. Only `T4_small` **match** stores
an anchor source decision id and its partition timestamp. T0 mints/replays, T3
matches, and T4 `new` create membership but do not authorize one. The source
decision must remain current and the entity active.

If exactly one active entity is anchored, a later reference with the exact
same normalized canonical name records an ordinary append-only T0 decision and
mention without T3 embedding or T4 generation. Its audit features retain the
document id, lemma, anchor contract, and source T4 decision id. Zero or several
active binding rows fail closed to the ordinary global cascade. Conflict is
not limited to anchors: a second same-name entity ever recorded in this
document remains a conservative binding row even if its decision is later
superseded. This keeps a prior same-name T4 `new` or human split from being
silently forgotten.

The projection primary key is `(deployment_id, doc_id, canonical_lemma,
entity_id)`, so lookup is bounded to one exact document/name prefix. An anchor
hit commits its mention, decision, and binding in the first lemma-locked
transaction. On the provider path the binding state is included in the
optimistic snapshot and revalidated under that lock.
Thus a T4 match committed while a peer model call is in flight invalidates the
peer's stale snapshot; its retry can take T0. No database transaction spans
provider latency.

The shortcut is gated by `deployments.document_binding_generation`. Existing
deployments remain on the normal cascade until setup rebuilds and verifies the
projection; new empty deployments bootstrap v1 directly. D102 rows rebuild
exactly from decision features. Older rows rebuild conservatively by expanding
the resolved entity's canonical aliases inside each document; false-positive
membership only disables the optimization. D74 deletes and verifies the
document's rows. Clearing the generation is the fail-safe repair action.
Human re-decision and unmerge restoration use the same binding writer. Normal
version or lineage deletion clears the entire document prefix so reingest does
not inherit an anchor grounded in deleted evidence.

## Costs and limitations

- Same-name people inside one document remain the deliberate hard case. Once
  an anchor exists, exact reuse does not inspect each later claim. A future
  extractor-supplied source-local entity id can disambiguate that case.
- A wrong first T4 match can be replayed. Append-only decisions and merge
  recovery remain available, but D102 does not improve T4 semantics.
- An inactive/merged anchor, invalid source decision, unready generation, or
  second active binding in the document disables the shortcut
  rather than guessing a redirect or winner.
- The projection is additional write and lifecycle work. It stores at most one
  row per distinct document/lemma/entity membership, no claim text or source
  surface, and is hash-partitioned by `doc_id` for exact-prefix pruning.
- The behavior changes provider-call counts and resolution decisions, so the
  resolver, normalizer component, and ordinary LoCoMo protocol generations
  must roll. No paid benchmark run follows from the design.

## Acceptance evidence

- First exact repeat reaches T4 and matches; later exact repeats in the same
  document record T0 and make no embedding or generation call.
- The same exact name in a different document still follows T0 candidates →
  T3/T4.
- Similar, phonetic, possessive, and differently canonicalized names never use
  the shortcut.
- T4 `new`, invalid source, inactive entity, unready generation, and
  conflicting binding state never authorizes T0.
- A concurrent T4 commit invalidates an in-flight stale snapshot and the retry
  uses the anchor.
- The T0 replay retains a normal mention and auditable source decision id.
