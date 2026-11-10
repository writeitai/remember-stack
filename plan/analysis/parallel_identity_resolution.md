# Parallel identity resolution without profile-change retries

2026-11-10. Non-binding analysis supporting D151. The owner authorized the
narrower commit checks and parallel ingestion; the accepted contract lives in
entity_identity_and_retrieval_design.md §3.3.2.

## Problem and inspected evidence

CascadeResolver.resolve snapshots candidate names, aliases, profiles, salient
facts, order and document-local bindings. It releases its transaction for T3/T4,
then reconstructs and compares the entire snapshot. Any difference repeats paid
work. The preceding conv-42 diagnostic recorded three normalization jobs that
exhausted retries while candidates for screenplay/play mentions were changing.
Serializing all LLM work avoided those races but serialized unrelated extraction
and grounding as well. This is an operational workaround, not an engine fix.

The relevant implementation is src/rememberstack/spine/resolver.py (resolve,
_snapshot_from_rows and _record); tests are in src/tests/spine/test_resolver.py.
The prior binding contract is D99 and D102, especially identity design §3.3.2.
Merge/unmerge use the deployment identity-epoch lock in clustering.py. Hard
forget's ordinary-work barrier and drain live in forget.py; normal deletion
locks the source lineage in lifecycle.py. These mechanisms must survive.

An entity ID identifies a referent, not its latest description. A candidate's
new observation need not invalidate a completed identity match. Conversely,
creating a new entity based on an old candidate set can duplicate an identity
another worker just created. These operations need different commit rules.

## Alternatives and chosen tradeoff

1. Keep whole-snapshot equality and lower all concurrency. Simple and already
   measured operationally, but unrelated work loses parallelism and continuously
   enriched popular entities can exhaust retries. Rejected.
2. Hold database locks during inference. This avoids optimistic races by blocking
   writers across provider latency, increasing transaction lifetime and preventing
   healthy parallelism. Rejected.
3. Accept every returned ID and every new-entity decision without checks. This
   can attach mentions to retired entities, race merge/unmerge, or duplicate
   concurrent creation. Rejected.
4. Commit an existing match against its recorded decision snapshot; separately
   recheck bounded identity membership for creation. Chosen. Ordinary profile or
   alias enrichment does not cause a retry. Hard source/status boundaries still
   apply. No global inference mutex, new queue, compatibility path or automatic
   dead-letter suppression is needed.

The chosen rule deliberately gives up the promise that the evidence supplied to
the model is unchanged at publication. A new candidate or distinguishing fact
can appear after inference begins. A match remains a decision about the supplied
snapshot, with the original candidate audit and an input digest. It is not
certified as a decision against every fact present at commit. Later merge/review
and new mentions can use newer evidence. This is an accuracy/throughput tradeoff,
not a claim of equivalent model outcomes; the tests establish race safety and
provider-call counts, not a corpus-wide accuracy gain.

## Commit boundaries, cost and failure behavior

Short transactions participate in the existing hard-forget fence, hold a shared
live-lineage row lock, then the shared identity epoch and normalized-lemma lock.
Provider calls hold none of these. The production resolver runs within ordinary
worker attempts, which hard forget drains before scrubbing; a preparing/active
forget blocks late publication. A deleted source cannot publish a new mention.

A selected entity is followed through merge redirects to an active root under
the identity lock. A missing, retired or broken target requires a fresh attempt;
routine evidence changes do not. The audit distinguishes the selected ID from
the committed canonical ID. The local T0 shortcut is still checked at the start;
a peer anchor arriving during a completed match does not erase that match.
Conflicting memberships are retained and disable later shortcuts normally.

For new decisions, re-read only the bounded candidate IDs and document binding
memberships under the lemma lock. A changed identity set retries against the
new candidates. Names may still identify different people; there is no unique
name constraint and no exact-name auto-match. Same-lemma creation commits are
serialized; different lemmas retain the existing bounded blocking recall limit.
This does not guarantee global duplicate elimination outside that recall.

No new raw profile copies are stored in audit JSON: a deterministic input digest
and existing bounded decision features preserve provenance without introducing
another erasure-sensitive prose store. Existing source/rationale retention rules
still apply. No credential, model, prompt, threshold or retrieval change is made.

The policy has a new resolver generation. Chunking, Selection, Claimify, chunk
and profile embedding identities, normalizer output keys and paid extraction
reuse stay unchanged. Existing accepted resolutions remain historical; no
automatic renormalization or re-ingestion is introduced. Match commits remove
the second profile/evidence hydration and avoid provider retries caused by
profile churn. Actual throughput and model quality remain measurements, not
promises. Tests must cover concurrent same-entity matches, competing creation,
merge, retirement, source deletion, invalid model IDs, bounded structural retries
and the unchanged Rule 4 reuse proofs.
