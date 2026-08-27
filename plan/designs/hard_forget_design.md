# Hard-forget design — one fail-closed lineage purge

> **Binding D98 amendment (2026-08-27).** There are no Ladybug/P2 snapshot
> prefixes, graph generations, local graph files, or `GraphRebuildWorker` in the
> forget inventory. Authority deletion plus the normalized PostgreSQL views
> removes forgotten material from all later live-graph statements. PostgreSQL
> backups and bounded in-flight MVCC snapshots follow the existing database
> erasure/retention contract. P3 clean rebuild remains required. Any P2 stage or
> object-prefix wording below is superseded by this rule and
> [`p2_graph_design.md`](p2_graph_design.md) §10.

> **Status:** current — D74; resolves `questions.md` #24 and gates WP-7.5.
> Normal deletion remains `evidence_lifecycle_design.md` §8 / schema §13.1. This document owns
> only irreversible hard-forget and restore non-resurrection.

## 1. Contract and boundary

Hard-forget removes one document lineage's contribution and source-bearing payloads from every
library-controlled active store. After completion, semantic, verbatim, graph, K, and browse reads
cannot surface content unique to that lineage, and their public negative is the same as for content
that never existed (S55). Information independently supported by another live lineage remains: the
operation forgets a source contribution, not every independently obtained copy of a fact.

> **Amended 2026-08-26 (D95–D96).** `profile_summary` and the profile embedding
> are a **derived cache**. Forgetting a lineage that contributed to a **shared**
> surviving entity must invalidate and recompute that cache from remaining
> evidence (or clear until recomputed), not only scrub exclusive entity ids.
> [`entity_identity_and_retrieval_design.md`](entity_identity_and_retrieval_design.md) §3.3.

The library owns:

- the portable, content-free forget manifest and its append/replay port;
- the fail-closed admission barrier;
- the PostgreSQL scrub and existing lifecycle/counting cascade;
- exact object-store, PostgreSQL P1-state, P3, and K purge hooks; live graph
  visibility follows the authority scrub in the same PostgreSQL database;
- idempotent replay after restore; and
- a deterministic S55 + restore canary.

The library does **not** choose backup schedules, retention periods, storage-provider lifecycle
rules, remote Git hosting, or legal policy. An adapter must make its declared active surface honor a
purge; physical backup expiry remains an operator/`ultimate-memory-cloud` concern under D60.

Hard-forget is lineage-scoped. Version deletion remains reversible/audit-preserving normal deletion.
Entity-wide or arbitrary-text erasure is an explicit non-goal and is not hidden inside this
workflow.

## 2. One durable intent: the portable forget manifest

The request is accepted only after a versioned `ForgetManifest` has been appended successfully
through `ForgetManifestPort`. The port is the durable source of forget intent; the PostgreSQL row is
an exact materialization plus execution progress, not an independent decision ledger.

The v1 manifest contains no source text, names, provider URIs, prompts, or prose. It contains only:

- schema version, `forget_id`, `deployment_id`, `doc_id`, and `requested_at`;
- a SHA-256 fingerprint of `(deployment_id, source_kind, source_ref)` when a stable source identity
  exists, plus every raw `content_hash` observed for the lineage;
- exact non-content row IDs whose P1 state must be removed after a restored
  PostgreSQL database (chunks, claims, relation
  and observation facts whose last live support was this lineage, and entities whose last live
  mention was this lineage);
- exact immutable object keys/prefixes that may contain the source;
- pre-forget P3 snapshot prefixes; and
- K artifact IDs whose body, curation sidecar, or history ever cited the lineage's evidence.

IDs and hashes are retained because replay must still work when PostgreSQL has already scrubbed the
payload columns or when only one external store was restored. The manifest is immutable and
idempotent by `forget_id`: appending the same bytes succeeds; reusing the ID for different bytes is
a typed conflict.

`ForgetManifestPort` has only two responsibilities: append one immutable manifest and enumerate
manifests for a deployment. Its storage must be outside the ordinary data-restore set it protects.
The self-host adapter uses a dedicated append-only local root; the cloud implementation and its
durability live in `ultimate-memory-cloud`. D75/WP-7.7 require operators to transfer this separate
manifest root before restored data can become readable; the library does not own byte transport.

External erasure capabilities are narrow protocols implemented by the
already-selected adapters: `ObjectPurgePort`, `CorpusFsPurgePort`, and
`KGitPurgePort`. Each takes a typed manifest subset and must be idempotent.
PostgreSQL P1 cleanup is part of the existing PostgreSQL scrub transaction, not
a separate adapter or store. The self-host and reference object/mount/K
adapters are required in WP-7.5; these are purge capabilities of D61's existing
stores, not alternative engines or new provider families.

### 2.1 PostgreSQL materialization and work identity

The schema adds one small, non-partitioned table and one worker stage:

```sql
CREATE TYPE forget_manifest_status AS ENUM ('preparing', 'accepted', 'complete');

CREATE TABLE forget_manifests (
  forget_id            uuid PRIMARY KEY,
  deployment_id        uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,
  schema_version        smallint NOT NULL,
  manifest_hash         text,
  manifest              jsonb,
  source_identity_hash  text,
  content_hashes        text[] NOT NULL DEFAULT '{}',
  status                forget_manifest_status NOT NULL DEFAULT 'preparing',
  prepared_at           timestamptz NOT NULL DEFAULT now(),
  accepted_at           timestamptz,
  completed_at          timestamptz,
  last_verified_at      timestamptz,
  UNIQUE (deployment_id, doc_id),
  UNIQUE (deployment_id, forget_id),
  CHECK ((status = 'preparing') OR
         (manifest_hash IS NOT NULL AND manifest IS NOT NULL AND accepted_at IS NOT NULL)),
  CHECK ((status = 'complete') = (completed_at IS NOT NULL))
);
CREATE INDEX ix_forget_source_guard
  ON forget_manifests (deployment_id, source_identity_hash)
  WHERE source_identity_hash IS NOT NULL;
CREATE INDEX ix_forget_content_guard ON forget_manifests USING gin (content_hashes);
```

`pipeline_component` gains `forgetter` and `pipeline_stage` gains the unlaned `hard_forget` stage.
Its one `processing_state` row uses the existing `document` target, `target_id = doc_id`, the
manifest hash as `content_hash`, and the registered `hard-forget-v1` component version. The table
holds the immutable manifest, irreversible ingest-guard columns, and three coarse lifecycle states;
attempts, traceback, retry, due time, and DLQ remain exclusively in `processing_state`.

## 3. Admission and request formation

Hard-forget is correctness-first and fail-closed:

1. A request serializes on the deployment's single forget operation, verifies deployment ownership,
   and preflights current authored K pages and compiled-page curation sidecars. If a cited path is
   not owner-redacted, the request returns those paths before changing admission. The library never
   invents replacement authored prose.
2. One PostgreSQL transaction inserts the `preparing` row. That row is the admission barrier: new
   public reads/ingests/admin mutations, ordinary work claims, mount publication, and unrelated
   rebuilds refuse with `forget_in_progress`. Already-running ordinary work is allowed to finish.
3. After the ordinary work ledger drains, the coordinator repeats K preflight against the current
   Git revision and takes the repeatable-read manifest inventory. Because ordinary commits and
   claims are now barred, this is the frozen acceptance cut. If the repeated preflight fails before
   any portable append attempt, the transaction deletes the `preparing` row and admission reopens.
4. The complete manifest and its hash are committed to the local row **before** calling the port.
   The coordinator then appends the exact bytes idempotently and marks the row `accepted`. Only that
   successful append makes the request accepted and enqueues the one `hard_forget` processing row.

A crash while the row is `preparing` is not an ordinary forgotten failure. If the manifest is not
yet stored, readiness repeats drain, final preflight, and inventory; once it is stored, readiness
retries the exact idempotent append. If no append was attempted and final preflight refuses, it
safely removes the row; after an append attempt starts, an ambiguous error stays fail-closed and
retries because the remote append may have succeeded. Enumeration also rematerializes a manifest
whose append succeeded just before the local status update.

The admission barrier is a **composition/perimeter rule**, not a guard inside every spine method.
It blocks traffic and ordinary work claims; it explicitly authorizes only the coordinator for that
`forget_id` and the coordinator's direct calls to the existing lifecycle,
PostgreSQL scrub, rebuild, K, and object services. Those internal calls do not re-enter public admission, so the purge cannot deadlock on its
own barrier. Hard-forget is rare; temporary deployment unavailability is the deliberate price for
one understandable correctness path.

The completed manifest remains an irreversible ingest guard. A later ingest with the same stable
source fingerprint or one of the forgotten content hashes is rejected rather than silently
resurrecting the lineage. There is no bypass flag. Deliberately accepting that material again is a
new policy decision and belongs in a new deployment, not an accidental restore path.

## 4. The one resumable workflow

One `hard_forget` processing row runs the following stages in order. Each stage is idempotent; the
ordinary work ledger supplies attempts, traceback visibility, retry, and DLQ handling. No campaign
table or deletion-specific scheduler is added.

1. **Normal lifecycle first.** Reuse the lineage-deletion/currency path: tombstone every version,
   end current testimony, recount shared facts, close facts with no current support, retire entities
   with no surviving mentions, and emit the existing K tombstone delta. This preserves D54's
   distinct-lineage counting and prevents hard-forget from becoming a second truth transition.
2. **PostgreSQL scrub and P1 cleanup.** Delete source-exclusive chunks,
   `chunk_search` rows, claims, occurrences, mentions, sections,
   extraction/audit payloads, review payloads, and evidence links. Clear source refs, titles, URIs,
   errors, free-text rationales/features, locators, and representation metadata associated only with
   the lineage. Shared facts and entities survive only when independently supported; exclusive
   observations are removed/scrubbed and exclusive entities remain non-readable retired handles
   with their names/aliases/profile text cleared. The content-free forget row, IDs, hashes, currency
   transition facts, and aggregate counts may remain. Entity-resolution audit payloads associated
   with any entity resolved from the lineage are content-cleared while their IDs and structural
   edges remain. The generic-identifier cache, planner audit transcripts, and legacy eval/golden
   fixtures have no reliable lineage provenance, so a rare forget conservatively clears those
   deployment-wide rather than guessing; they are derived/control/test data and can be rebuilt.
   Clear the derived embedding columns on surviving claim/fact/entity rows when
   their input text changed or was scrubbed. These changes commit in the same
   PostgreSQL transaction; there is no separate P1 compaction or acknowledgement.
3. **Raw and artifact objects.** `ObjectPurgePort` deletes every manifest raw, artifact, asset, and
   transcript key/prefix. A deduplicated content object is deleted only when no other live lineage
   references it; otherwise the other lineage remains the lawful owner of that identical byte
   object. Missing keys count as success. P3 snapshot prefixes are handled in stage 4. The
   committed PostgreSQL scrub immediately removes the lineage from later live graph statements.
4. **P3.** Use `CorpusFsBuilder` to publish a clean snapshot from the scrubbed spine. Then delete
   every manifest-listed older P3 prefix and its registry rows. Mounts may reopen only the new
   pointer; self-host cache cleanup is part of `CorpusFsPurgePort` acknowledgement, not a
   best-effort side task.
5. **K.** Compiled pages recompile through the existing driver without the forgotten evidence.
   `KGitPurgePort` then removes every affected body/curation path from all reachable Git history and
   re-adds only its already-sanitized current file. This intentionally discards unrelated history
   of an affected path rather than risking residual source text. Authored/curation preflight makes
   this mechanical; the library never rewrites their prose. Archived writer/planner transcripts in
   the manifest are object-store payloads and were deleted in stage 3.

   The v1 PostgreSQL citation table represents current bindings and cannot prove that a page never
   cited the lineage in older Git history. Rather than add a second historical-citation ledger,
   hard-forget conservatively nominates every registered K artifact and archived K transcript in
   the deployment. Sanitized current files are re-added unchanged, but their prior path history is
   discarded. This is an intentional correctness-first cost of the rare irreversible operation.
6. **Verify and reopen.** Production verification proves every manifest ID, hash, key/prefix, old
   P3 version, P1 derived state, live-graph reachability, and forbidden K reference is absent from its declared active store;
   PostgreSQL has no remaining nominated source-bearing payload. Those mechanical store checks make
   public lookup by the forgotten IDs return the ordinary never-existed negative; the handler does
   not self-call a serving surface as a second verifier. The planted unique token and five-channel
   envelope equality are test-fixture assertions, not production inputs. Only then is the manifest
   marked complete and the deployment barrier opened.

If any adapter throws or verification fails, the original exception remains visible, the work row
retries/dead-letters normally, and the barrier remains closed. There is no partial-success response.

## 5. Restore non-resurrection

Every serving composition has one readiness step before it accepts traffic:

1. enumerate the deployment's portable manifests and materialize any missing PostgreSQL rows;
2. for **every** manifest, including locally `complete` records, re-honor its
   PostgreSQL authority/P1/live-graph state, exact objects, old P3
   cache/mount, and K erasure through the idempotent purge capabilities;
3. replay the full spine + clean-rebuild workflow when PostgreSQL is unsanitized or its current
   P3 pointer does not resolve to a verified clean snapshot built after the accepted
   manifest; and
4. run mechanical production verification, update `last_verified_at`, and only then report ready.

`complete` means the workflow once succeeded; it never means external stores may be skipped
forever. Cheap exact deletes are reissued. The K adapter records a store-local acknowledgement ref
after history erasure and validates it on every `honor` call; losing/restoring that ref triggers the
same path erasure again. Such acknowledgements are receipts/cache only—the portable manifest is the
sole intent. The P3 adapter likewise deletes every manifest-listed old durable prefix and local
serving copy even when PostgreSQL still points at a clean current snapshot.

Readiness stays false until re-honor/replay and S55 verification complete. Therefore restoring an older
PostgreSQL dump, object bucket, P3 snapshot, K remote, or any combination cannot make
forgotten data queryable: the append-only manifest lives outside that restore and re-closes the
barrier first. The restore path is not special purge machinery; it feeds the same worker.

An operator that omits or loses the manifest store cannot truthfully claim safe restore. An
unavailable or unprovisioned store is a hard readiness failure. A reachable but wrongly substituted
empty root cannot reveal the manifests it lost, so verifying the transferred manifest root remains
an operator obligation under D75. Provider backup deletion and expiry remain operational
obligations, but no provider backup may be mounted as an active serving store without this replay
gate.

## 6. Rejected complexity and explicit limitations

- No distributed transaction or rollback across PostgreSQL, object storage, snapshots, and Git.
  P1 derived state can be purged in the PostgreSQL transaction; append-first + idempotent replay +
  fail-closed admission handles the remaining boundaries.
- No semantic "find similar private text" eraser. Exact lineage provenance, IDs, keys, citations,
  and source/content fingerprints are the boundary.
- No soft-delete mode hidden in hard-forget. Normal deletion already owns reversible audit history.
- No backup scheduler, retention setting, provider lifecycle policy, deletion dashboard, or hosted
  control plane in the library.
- PostgreSQL physical backups/PITR now include `chunk_search.search_text`, a
  normalized source-body copy. The portable manifest and restore re-honor gate
  scrub restored PostgreSQL P1 state before admission; provider backup expiry
  remains the operator obligation above.
- No machine-authored redaction of authored K content. A typed preflight makes accountable
  redaction a prerequisite instead of leaving a half-complete operation.
- No claim that immutable P3 snapshots purge "for free." A clean rebuild changes the pointer;
  explicit deletion removes the old bytes and local serving copies. The live graph has no
  separate immutable bytes.
- No second K citation-history ledger solely for erasure. V1 conservatively rewrites registered K
  paths because current citation bindings cannot establish historical non-citation.

## 7. WP-7.5 acceptance

The deterministic S55 gate is intentionally compositional rather than one monolithic fixture: a
real-PostgreSQL catalog canary proves inventory/scrub/residual SQL, including
`chunk_search` and in-row derived vectors; a real
self-host canary proves filesystem/P3/Git purge and independent restore re-honor, and a small coordinator
canary proves ordering, fail-closed behavior, and never-existed envelope equality. They share the
same typed manifest and handler contracts. This keeps each failure attributable while together
planting a unique token in one lineage and independently supported control facts and proving:

1. proves authored/curation preflight blocks before acceptance;
2. redacts those owner-controlled files and completes hard-forget;
3. proves the unique token and source-exclusive IDs are absent everywhere while independent facts
   survive;
4. proves forgotten and never-existed public envelopes are equal;
5. restores the whole pre-forget fixture while retaining the manifest root and proves readiness
   blocks, replays, and returns S55 to green; and
6. after a completed forget, independently restores object storage, P3
   serving copies, and K while leaving PostgreSQL `complete`, proving each
   readiness pass re-honors the manifest; and separately restores a pre-forget
   PostgreSQL database and proves its authority plus `chunk_search`/in-row
   vectors are scrubbed before readiness.

The canary uses deterministic providers and makes no hosted LLM calls. Focused component tests cover
manifest idempotency/conflicts, ingest guards, missing-object idempotency, adapter failure visibility,
and the barrier never reopening on a failed stage.
