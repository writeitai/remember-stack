# Corpus document folders — implementation order (D155)

Date: 2026-11-13. Implementation sequence for the accepted design; the design
amendment itself shipped no runtime changes. Authority: [E0 §§5–7](../designs/e0_files_design.md#5-mounting--the-corpus-is-the-customer-filesystem),
[D155](../../decisions.md#d155-the-corpus-filesystem-is-a-mutable-tree-of-document-folders).
[Analysis](../analysis/corpus_document_folders.md) records the tradeoffs.

Each numbered slice is a separate, independently mergeable PR with its stated
tests. Supporting capabilities merge before the serving profile switches;
activation includes deletion, forget and readiness. No old-tree migration,
compatibility alias or dual publication. Public docs change when behavior ships.
The six slices need no reservation, revision/journal/receipt tables or id index.

## 1. Object operations and provider-independent contract

Add streamed replacement and fully paged listing beside immutable `ObjectStorePort`;
reuse exact idempotent deletion. Implement in S3/local-FS adapters, leaving E0
create-only writes intact. Stream across endpoints; abort/clean failed multipart
uploads. Keep local class bookkeeping outside reader paths. No rename, server-side
copy requirement or native GCS client.

Tests: real **MinIO** replace/create/delete/missing-key contracts, path boundaries,
empty prefix, multiple listing pages and missing/repeated tokens. Include a
multipart-sized streamed original, failure cleanup, bounded memory, unchanged E0
overwrite rejection and storage-class/transfer accounting. Wire MinIO explicitly
regardless of the ordinary Compose profile's provider.

## 2. Selection, deterministic suffixes and pure reader export

Implement `sha256(doc_id.bytes).hexdigest()[:24]`, ordered types, safe topic/slug
rules, reader-kind allowlist, active-generation selection and bounded navigation
indexes/sharding as a renderer. Add only nullable `documents.corpus_published_revision`;
no suffix allocation, counter, request/progress/member tables or `_paths/` tree.
Read recorded E0 URIs/inventories, including registered-upload keys and active
PageIndex. Compute desired export digests from DB inputs and builder policy.

Tests: deterministic suffix before/after scrub with no stored suffix; all type
branches, independently typed attachments, hostile/empty/non-ASCII/long titles,
intentional null/invalid topics, reader assets included and internals excluded.
Compare original/Markdown/source-map/PageIndex bytes to authority and resolve
relative links. v1 served plus v2 stored/parked exports only v1; raw-only has no
folder. Cover navigation split/merge, many topics, bounded member/asset tables
and byte-identical output for identical DB state. Page splits/merges must change
only navigation metadata, keeping physical document paths and bytes in place.

## 3. Idempotent publication, ordinary work and reconcile

Implement `publish_corpus(doc_id)` with the deployment advisory lock: re-read DB,
clear the marker, page through suffix matches, stream desired files first, delete
other document folders, regenerate ancestors/root from DB, then set the marker
only if inputs still match. Scan stale navigation entries by doc id/suffix when
retry has lost an old folder, and prune empty branches from DB membership.
Reconcile uses the same code and enumerates all indexes/orphans. Check the same
lock session and live claim before each mutation, including multipart requests;
bound S3 connect/read timeouts. Store expected SHA-256 metadata on every object.
Verifying reconcile rewrites missing/mismatching digests even when the document's
marker matches: a late in-flight request after DB-session loss can transiently
leave stale bytes, and the marker means current inputs were published once.
This accepted rare race adds no coordination machinery. Bind the handler
to tests, leaving profile activation to slice 4.

In each relevant authoritative transaction enqueue a fresh event target in the
existing unlaned `processing_state` stage, payload `doc_id` only. No request table,
durable desired revision, reset attempts or per-event component policy. Reuse
announcements, retries, leases, DLQ and lost-wake recovery; execute with both
self-host class claims and D147 pushed `run_task`. Policy seeding uses the same path.

Tests: **MinIO-backed** create/update/title-topic-type move/delete and exact bytes/
index links after retry. Inject failures between steps, especially deletion of the
last old-folder key before index regeneration; replay duplicate/reordered events,
A→B→A, changing authority mid-copy, two writers sharing ancestors, deletion during
publish, and lease/lock loss with in-flight IO. Confirm stale shared indexes are
regenerated from DB. Incremental edits and empty-store reconcile must converge to
the same keys/hashes. Ordinary edits never recopy unrelated originals/media.
Test transactional enqueue rollback, rejection of inactive-policy work, publish
after an earlier succeeded event,
parked uploads, every layer profile, current-policy seeding, announcements and
lost delivery; no hosted models. Register/test the same pushed class map in the
cloud repo as integration, without making engine tests depend on it.


Implementation: [PR #607](https://github.com/writeitai/remember-stack/pull/607)
builds the slice-3 capability with the handler bound only in tests. The companion
[cloud PR #1017](https://github.com/writeitai/ultimate-memory-cloud/pull/1017)
checks the existing non-convert pushed class; engine tests do not depend on it.
The publisher uses a conservative pass over all shared navigation metadata,
regenerating it from DB membership and skipping digest-equal writes. This covers
stale index-only doc-id/suffix rows without parsing escaped old Markdown; it
never recopies unrelated originals/media. The real MinIO matrix lives in
[`test_minio_corpus_publication.py`](../../src/tests/adapters/test_minio_corpus_publication.py)
and the late-request repair regression in
[`test_minio_corpus_lock_loss.py`](../../src/tests/adapters/test_minio_corpus_lock_loss.py).
Profile activation and periodic verification remain slice 4.

## 4. Activate with deletion, forget and restore together

Switch both profiles to the new publisher/reconcile and marker-based readiness.
Schedule periodic digest-verifying reconcile to repair rare late-request damage.
Normal delete completes per-document removal before success or leaves durable
retry work and reports failure. Adapt D74's existing purge capability: keep intent,
barrier, drain/fence and verification; compute the suffix from manifest `doc_id`,
erase that document's copies/uploads/caches, then regenerate/prune navigation
indexes from scrubbed DB authority. Never reset the bucket or recopy survivors.
Use E0 §6.7's object-version erasure rule. Remove snapshots/pointers and obsolete
mount composition. Replay all manifests before readiness, including complete ones.

Tests: extend hard-forget handler/self-host/S55 fixtures. The mandatory
**PostgreSQL + real MinIO forget/restore test** plants a unique token in original,
Markdown, media, title/topic, member/root indexes, a former moved folder and an
interrupted unindexed copy. Keep an independent control document; assert its keys/
bytes stay present and its originals/media have **zero** delete/replacement calls
through forget and retry. Scan/read the corpus to prove the forgotten token,
suffix objects and index mentions are absent. Inject delete, multipart, cache-ack,
index-regeneration and verification failures; keep the barrier closed and prove
retry plus delayed ordinary work cannot resurrect the source.

Keep pre-forget fixtures outside active storage. Independently restore bucket
alone with PostgreSQL still `complete`, DB alone with a clean bucket, and both,
retaining portable intent. Replay suffix erasure and index regeneration before any
API/controlled mount reopens; verify old paths and source-bearing versions are gone
and control bytes remain in place. Include delete-marked old-folder keys and old
shared-index versions in the retained-version fixture. Include session-cache acknowledgement and
retained-version purge where enabled; do not mock the storage purge. No model calls.

## 5. Consumption paths, readiness and documentation

Use the nullable-marker guard for MCP/API/SDK `p3_path` and readiness, including
periodised/history results. Teach MCP resolution and offline suffix `find`; no
path map or id index. Update the consumption skill/navigation evaluation and all
shipped mount/readiness docs, including `/docs/project/not-built-yet`.

Tests: null path before/during/after failed publish, matching path on success,
v1 served with pending v2, raw-only, deleted, historical and no-edition-in-force
selections. Suffix lookup survives folder moves, index-page changes and metadata scrub; asset
path components cannot impersonate another document. Unrelated success cannot
satisfy readiness. Exercise `llms.txt → indexes → originals/derivatives`, claims/
facts switches, standalone K and exact-version `source_open`. Update default and
layers-off catalogue fixtures. Benchmark attestation uses markers plus ready-folder
identities/frozen inventories with a fresh protocol; old evidence stays source-pinned.

## 6. Mount wrapper and provider qualification

Implement the thin `remember mount DIR` / `remember unmount DIR` wrapper with the
cloud ticket/session integration. Test stdin-only tickets, direct invocation,
JSON/exit/missing-executable behavior and failed/expired tickets. Unmount gets no
new ticket. Keep self-host instructions provider-neutral and read-only.

Run adapter conformance against credentialed AWS S3, GCS XML/HMAC and mountable.io
sandboxes; record signing/addressing/checksum/multipart results, differing store
endpoints, access logs and read-only session/cache erasure acknowledgement. Check
mountable bucket=filesystem-id. MinIO remains the repeatable engine gate, without
claiming that it qualifies other providers.

Qualify purge on providers without `ListMultipartUploads`: mountable's gateway
returns `NotImplemented`/501 and sweeps unfinished uploads after seven days.
Verify published-object deletion/absence still succeeds and evidence explicitly
reports `multipart_uploads="provider_managed"`, never checked absence of parts.
Record the deployed provider's expiry/sweep guarantee and check that own failed
uploads still abort. Exercise continuation-token `ListObjectsV2`, `Content-MD5`
on PUT/upload-part and immutable `If-None-Match: *` against each endpoint; the
[source audit](../analysis/corpus_document_folders.md#mountable-upload-listing-compatibility-pr-603)
supports these mountable operations but does not replace sandbox qualification.

## Cross-slice gates

Preserve Rule 4: two dated versions with unchanged sections before/after an edit
keep their provider-call counts and identities. Run the existing reuse lifecycle/
text-origin proofs appropriate to implementation; never put suffixes, markers or
export metadata in paid-work keys. Only nullable-placement output/schema generation
changes, reusing stored summaries; topic/title-only publication makes no model
calls. Keep known Rule-4 gaps explicit. Each PR records actual checks/provider
limits, and no new serving path bypasses D74.
